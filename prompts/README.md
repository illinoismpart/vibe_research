# Research prompts

This folder is for **research questions and example prompts** you use when querying your corpus. Keeping them here does three things:

1. **You** have a running log of what you asked and how you asked it.
2. **AI agents** that read this repo can see the kind of inquiry you care about and stay on task.
3. **Others** who fork the template get concrete examples of grounded, provenance-aware prompting.

## How to write prompts that stay grounded

- **Ask for citations.** Explicitly request that every claim be tied to a document in the manifest (filename + SHA256 + page or section when available).
- **Scope to the corpus.** Phrase questions in terms of "in the ingested documents" or "across the parsed files" so the agent does not reach for general knowledge.
- **Allow "we don't know."** Ask the agent to say when the corpus doesn't support an answer. That keeps the boundary between inference and fact clear.
- **Request structure when it helps.** Ask for tables, bullet lists, or JSON when you want to reuse or audit the output.

See `example_inquiry.md` for annotated examples you can adapt.
