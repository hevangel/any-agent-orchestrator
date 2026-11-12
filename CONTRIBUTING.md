# Contributing

Thanks for helping. This project builds an orchestrator for AI coding agents, and it is built the same way: by AI agents.

## Humans do not touch the code

Every change to the files in this repository is written by an AI coding agent. Humans do not write or edit code, tests, scripts, configuration, data or docs by hand — not even a one-line fix or a typo. If you want something changed, ask your agent to do it.

What humans do:

- Open issues: report bugs, request features, describe what you want.
- Steer agents: write the prompt, choose the agent, answer its questions.
- Review pull requests and comment on them.
- Use released versions and report what they find.
- Maintainers merge pull requests and mark released work Human-reviewed.

Any coding agent is welcome: Claude Code, Codex, Cursor, Copilot, Kiro, OpenCode or anything else. Name it in the pull request (see below).

## Shift-right quality

Quality assurance happens after the release, by the people who use it. Agents build each change and review it (an AI review, recorded in the issue). The release then ships with no human gate before it. Humans test by using the released version.

So if you find a bug:

1. Open an issue titled `[Bug] …` with the `bug` label. Say which release you used, what you did, what you expected and what happened.
2. Better still, fix it yourself: point your AI coding agent at the issue and send a pull request.

## Pull requests

The standard GitHub flow:

1. Fork the repository (or create a branch if you have write access). Never commit to `main`.
2. Have your agent read [AGENTS.md](AGENTS.md) and the spec for the area it changes in [spec/](spec/) before it starts.
3. Keep one pull request per issue, and link it (`Fixes #123`).
4. Run the checks before pushing: `uv run pytest -q` at a minimum. Run e2e tests (`uv run pytest -m e2e`) only if the change needs them; they spend tokens.
5. In the pull request description, name the agent and model that wrote the change, and list exactly which checks ran and their results.
6. Address review comments, again through your agent.
7. A maintainer merges after CI passes and the review is done.

Work queue, issue fields and labels: [spec/github_project.md](spec/github_project.md).

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
