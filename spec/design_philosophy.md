# Design philosophy

The principles behind any-agent-orchestrator. When a design choice is unclear, choose the option that fits these; when a spec conflicts with them, raise it in an issue.

1. **Avoid vendor lock-in.** No lab, cloud, agent harness or storage service is required. Every integration has an alternative: herdr, web or desktop for agents; this computer, SSH machines or lab-hosted clouds for compute; Dropbox or Box for the mailbox. Adding or dropping a vendor is a catalog entry (`agents.json`, `.env`), not a redesign.

2. **LLMs are fungible.** A model is chosen for the best trade-off between token cost and intelligence for the task in hand, not out of loyalty to a vendor. Routing uses measured data — quotas, usage, API-equivalent cost, free-token offers — and the Agent field names the harness, never a model family.

3. **Code committed to the GitHub repository is the source of truth.** Not chat history, not an agent's memory, not a local working tree. Specs, prompts, data and decisions that matter are committed. An agent that has not pushed its work has not done it.

4. **The build-out is managed with GitHub issues and a GitHub Project, using project-management principles.** Units of work are issues, dependencies are native `blocked_by` edges, order is Iteration, releases are milestones, and the comment trail is the evidence ([github_project.md](github_project.md)).

5. **Tokens and compute are resources to be managed.** Subscription quota, API spend and machine time (CPU, memory, VMs) are tracked like any budget ([dashboards.md](dashboards.md), [machines.md](machines.md)). Within limits they are interchangeable: a cheaper, less capable model can reach the same result by spending more compute — more attempts, more tests, more review rounds. The orchestrator makes that trade explicitly.

6. **Know where every agentic loop runs, and what that machine can do.** A loop runs on this computer, on a remote host, or in a VM owned by an AI lab. A cloud agent is just someone else's computer: under the hood it is still a machine with an OS, a filesystem, a network policy and limits. The orchestrator records which machine each run is on and what that environment offers — internet access, whether it can install software and Python or Node.js packages, free web search, scraping, browser control, desktop control, GPU, how long a session may live — and dispatches work only where those capabilities exist ([machines.md](machines.md)). It never assumes a capability; it checks.

7. **It works as a command center or as an observer.** As a command center it launches and drives agents itself. As an observer it only watches: a human opens any AI app (on a phone, for example) and says "work on GitHub issue N", and the orchestrator tracks the result through the project, the repository and the usage data. Both modes have to work; neither may be required.

8. **Build from first principles; do not depend on any protocol.** Agent protocols and plugin standards come and go. The orchestrator talks to agents through what every one of them already has — a terminal, a web page, a desktop window — and coordinates through plain artifacts: git, GitHub issues, files in a shared folder. A protocol may be used where it helps, but nothing may stop working without it.

9. **Batch orchestration, not real time.** Agents are dispatched and checked every few hours, once a day or every few days. Latency does not matter; throughput, cost and correctness do. The orchestrator is built for crazy, big ideas that are too large to speed-run, so it optimizes for work that runs for weeks, survives restarts and picks up where it left off.

10. **The human builds it first, then learns it.** The human directs agents to build the system without hand-writing code ([CONTRIBUTING.md](../CONTRIBUTING.md)), then learns how it works from the committed code, specs and issue history. That only works if the repository explains itself: keep specs current, issues traceable and decisions written down.
