---
title: Sidekick
emoji: 🤝
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: A personal co-worker agent with a browser and an evaluator
---

# Sidekick

A personal co-worker agent. Give it a task and a definition of success, and it works with a real browser, a sandbox filesystem, web search, Wikipedia and push notifications until it meets your criteria or needs to ask you something.

(The block above this heading is configuration for Hugging Face Spaces.)

## How it works

- **Worker:** a LangChain `create_agent` with middleware for planning, PII redaction, a limit of 30 model calls per run, and human approval before push notifications go out.
- **Evaluator:** after each attempt, a second model checks the answer against your success criteria and sends the worker back with feedback, up to three attempts.
- **Tools:** Serper search, Wikipedia, Pushover, and Playwright and filesystem MCP servers held open in persistent sessions.
- **UI:** a Gradio chat with a live plan panel and an Approve button that appears when the Sidekick pauses for you.

For the full design, including data flows, security and known limits, see [docs/architecture.md](docs/architecture.md).

| File | Purpose |
| --- | --- |
| `app.py` | Gradio UI and entry point |
| `sidekick.py` | Worker, evaluator loop and middleware |
| `sidekick_tools.py` | Tools and MCP sessions |
| `styles.py` | Theme and CSS |
| `package.json` | Pinned MCP server versions |

## Settings

Copy `.env.example` to `.env` and fill it in. Never commit `.env`; it is ignored by Git and left out of Docker images.

| Setting | Required | Purpose |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes | The worker and evaluator models |
| `SERPER_API_KEY` | Yes | Web search |
| `PUSHOVER_TOKEN`, `PUSHOVER_USER` | No | Push notifications |
| `SIDEKICK_USERNAME`, `SIDEKICK_PASSWORD` | On Spaces | Login for the UI |
| `SIDEKICK_HEADLESS` | No | `true` runs the browser with no window. The Docker image sets this. |

## Run locally

The browser opens in a window on your desktop, so you can step in for logins and captchas. You need [uv](https://docs.astral.sh/uv/), Node 24 or later, and Chrome.

```
uv sync
npm ci
uv run app.py
```

To run the tests: `uv run pytest`.

## Run in Docker

The browser runs headless inside the container. You need Docker.

```
docker compose up --build
```

Open <http://127.0.0.1:7860>. Files the Sidekick writes appear in `./sandbox` on your machine.

## Deploy to Hugging Face Spaces

1. Create a new Space at <https://huggingface.co/new-space> and choose **Docker** as the SDK. **Private** is the safer choice.
2. In the Space's **Settings > Variables and secrets**, add `OPENROUTER_API_KEY`, `SERPER_API_KEY`, `SIDEKICK_USERNAME` and `SIDEKICK_PASSWORD` as secrets, plus the Pushover keys if you use them.
3. Push this repository to the Space, using a Hugging Face access token with write permission as the password:

   ```
   git remote add space https://huggingface.co/spaces/<your-username>/sidekick
   git push space main
   ```

The Space builds the `Dockerfile` and starts on port 7860. If the login page keeps reloading inside the Hugging Face page, open the app at its direct `https://<your-username>-sidekick.hf.space` address.

### How a deployed Sidekick differs

- **It spends your money.** Anyone who gets past the login can use your API keys and drive a browser from your Space. The app refuses to start on a Space with no login set.
- **Nobody can step in to help the browser.** `request_human_help` is removed in headless mode, so tasks that need a login or captcha will report that they could not continue. Approving push notifications still works.
- **Files are out of reach and temporary.** The UI has no download link, and the Space's disk is wiped on restart, so ask for results in the reply rather than in a file.
- **All sessions share one sandbox folder**, and each open browser tab starts its own Chromium. This suits one person, not a team.
- **Expect more blocks.** Travel sites and others often refuse traffic from data centers, so errands that work on your desktop can fail on a Space.

## Credits

Based on the Sidekick project from Ed Donner's [Agentic AI course](https://github.com/ed-donner/agents), used under the MIT License. See [LICENSE](LICENSE).
