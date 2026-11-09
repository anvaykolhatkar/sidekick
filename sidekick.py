"""The Sidekick: a create_agent worker wrapped in a homemade evaluator loop.

The worker is a single create_agent (Layer 3). Around it we run our own loop that checks
the worker's answer against the user's success criteria, and either accepts it, sends it
back for another attempt, or returns to the user with a question. Middleware gives the
worker a plan it shares with the UI, guardrails for PII and runaway costs, and a pause
for human approval before sensitive actions.
"""

import asyncio
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    PIIMiddleware,
    TodoListMiddleware,
    dynamic_prompt,
)
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command

from sidekick_tools import HEADLESS, get_all_tools

load_dotenv(override=True)

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(HERE, "sandbox")
MAX_ATTEMPTS = 3


class EvaluatorOutput(BaseModel):
    feedback: str = Field(description="Feedback on the assistant's response")
    success_criteria_met: bool = Field(description="Whether the success criteria have been met")
    user_input_needed: bool = Field(
        description="True if the assistant has a question, needs clarification, or is stuck and needs the user"
    )


WORKER_PROMPT = """You are Sidekick, a capable personal assistant who completes tasks for the user.
You have a real web browser, a sandbox filesystem, web search, Wikipedia, and the ability to send push notifications.
When you use the browser, navigate to a page and read it with a snapshot rather than clicking around unnecessarily.
Dismiss cookie banners and popups yourself by clicking in the browser.
{human_help}
For flight searches, use Google Flights in your browser: go straight to https://www.google.com/travel/flights?q=...
with a natural language query like "flights from New York to London leaving 14 July returning 21 July".
Keep working on the task until the success criteria are met, or until you genuinely need to ask the user a question.
If you have a question, ask it plainly. When you are finished, give your final answer clearly,
saying what you did, what you produced, and what you found."""

HUMAN_HELP_HEADED = """If you reach something only a human can do, like logging in, a captcha, or two-factor
authentication, use the request_human_help tool to tell the user exactly what to do in your browser window,
then carry on once they have done it."""

HUMAN_HELP_HEADLESS = """Your browser runs on a server where the user cannot see or touch it. If you reach something
only a human can do, like logging in, a captcha, or two-factor authentication, try another source that does not
need it, and if there is none, tell the user plainly that you could not get past it."""


class TolerateToolErrors(AgentMiddleware):
    """Hand tool failures back to the model as a message so it can recover, rather than
    crashing the run. Tools that touch the outside world, like a browser, fail now and then."""

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as error:
            return ToolMessage(
                content=f"That tool call failed: {error}. Try another approach.",
                tool_call_id=request.tool_call["id"],
            )


class AnswerDanglingToolCalls(AgentMiddleware):
    """Close off tool calls that never got a result, so the model API accepts the history.
    They are left behind when the user sends a new message instead of approving a paused
    action, or when a run is cancelled partway through a tool. The tool itself is not run."""

    def before_agent(self, state, runtime):
        messages = state["messages"]
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        repaired = []
        for message in messages:
            repaired.append(message)
            if isinstance(message, AIMessage):
                repaired += [
                    ToolMessage(
                        content="This tool call was not run: the user moved on before it completed.",
                        tool_call_id=call["id"],
                    )
                    for call in message.tool_calls
                    if call["id"] not in answered
                ]
        if len(repaired) == len(messages):
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}


class Sidekick:
    def __init__(self):
        self.sidekick_id = str(uuid.uuid4())
        self.memory = InMemorySaver()
        self.tools = None
        self.sessions = None
        self.worker = None
        self.evaluator = None
        self.task = ""
        self.success_criteria = ""
        self.attempts = 0
        self.paused = False
        self.pending_actions = 0
        self.todos = []

    async def setup(self):
        os.makedirs(SANDBOX, exist_ok=True)
        self.tools, self.sessions = await get_all_tools(SANDBOX)
        worker_prompt = WORKER_PROMPT.format(human_help=HUMAN_HELP_HEADLESS if HEADLESS else HUMAN_HELP_HEADED)
        interrupt_on = {"send_push_notification": True}
        if not HEADLESS:
            interrupt_on["request_human_help"] = True

        @dynamic_prompt
        def prompt_with_current_time(request):
            return f"{worker_prompt}\nThe current date and time is {datetime.now().astimezone():%A %d %B %Y, %H:%M %Z}."

        self.worker = create_agent(
            model="openai:gpt-5.4-mini",
            tools=self.tools,
            middleware=[
                prompt_with_current_time,  # first, so the middleware below can add to this prompt
                AnswerDanglingToolCalls(),
                TolerateToolErrors(),
                TodoListMiddleware(),
                PIIMiddleware("email"),
                PIIMiddleware("credit_card", apply_to_tool_results=True),
                ModelCallLimitMiddleware(run_limit=30),
                HumanInTheLoopMiddleware(interrupt_on=interrupt_on),
            ],
            checkpointer=self.memory,
        )
        self.evaluator = ChatOpenAI(model="gpt-5.4-mini").with_structured_output(EvaluatorOutput)

    async def evaluate(
        self, message: str, success_criteria: str, last_reply: str, tools_used: list[str]
    ) -> EvaluatorOutput:
        prompt = f"""You decide whether an assistant has met the success criteria for a task.

The user's request was:
{message}

The success criteria are:
{success_criteria}

The tools the assistant called while working, in order:
{", ".join(tools_used) or "none"}

The assistant's most recent reply was:
{last_reply}

Decide whether the success criteria are met, using the tool calls as evidence of what was actually done.
Also decide whether the assistant needs more input from the user, either because it asked a question,
needs clarification, or seems stuck. Give brief, concrete feedback."""
        return await self.evaluator.ainvoke(prompt)

    async def run_turn(self, message: str, success_criteria: str, history: list) -> list:
        """One turn of conversation: the worker attempts the task and the evaluator checks it,
        retrying with feedback up to MAX_ATTEMPTS. If the worker pauses for approval, this
        returns straight away with paused set, and resume() continues the same turn."""
        self.task = message
        self.success_criteria = success_criteria or "The answer should be clear, correct and complete"
        self.attempts = 0
        self.todos = []
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": f"{message}\n\nThe success criteria for this task are: {self.success_criteria}",
                }
            ]
        }
        return await self._advance(payload, history + [{"role": "user", "content": message}])

    async def resume(self, history: list) -> list:
        """Approve the actions the worker paused on, and continue the turn."""
        payload = Command(resume={"decisions": [{"type": "approve"}] * self.pending_actions})
        return await self._advance(payload, history)

    async def _advance(self, payload, history: list) -> list:
        config = {"configurable": {"thread_id": self.sidekick_id}}
        keep_browser = False  # only while the user still has to act on this task
        try:
            while True:
                result = None
                async for result in self.worker.astream(payload, config=config, stream_mode="values"):
                    self.todos = result.get("todos", self.todos)

                if "__interrupt__" in result:
                    actions = result["__interrupt__"][0].value["action_requests"]
                    self.paused = True
                    self.pending_actions = len(actions)
                    keep_browser = True
                    described = "\n".join(action["description"] for action in actions)
                    return history + [{"role": "assistant", "content": f"Waiting for your approval:\n{described}"}]

                self.paused = False
                reply = result["messages"][-1].content
                tools_used = [
                    call["name"] for m in result["messages"] for call in (getattr(m, "tool_calls", None) or [])
                ]
                self.attempts += 1
                verdict = await self.evaluate(self.task, self.success_criteria, reply, tools_used)
                if verdict.success_criteria_met or verdict.user_input_needed or self.attempts >= MAX_ATTEMPTS:
                    keep_browser = verdict.user_input_needed and not verdict.success_criteria_met
                    return history + [
                        {"role": "assistant", "content": reply},
                        {"role": "assistant", "content": f"Evaluator: {verdict.feedback}"},
                    ]
                payload = {
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Your last response did not meet the success criteria. "
                            f"Here is the feedback: {verdict.feedback}. Please keep working and address it.",
                        }
                    ]
                }
        finally:
            if not keep_browser:
                await self._close_browser()

    async def _close_browser(self):
        """Close the browser window at the end of a task. The MCP server stays up, and the
        next browser tool call opens a fresh window."""
        browser_close = next((tool for tool in self.tools if tool.name == "browser_close"), None)
        if browser_close:
            try:
                await asyncio.wait_for(browser_close.ainvoke({}), timeout=10)
            except Exception:
                pass  # the task's result matters more than a tidy close, and cleanup() still ends it

    def cleanup(self):
        """Shut down the MCP servers; the browser window closes."""
        if self.sessions:
            self.sessions.stop()
