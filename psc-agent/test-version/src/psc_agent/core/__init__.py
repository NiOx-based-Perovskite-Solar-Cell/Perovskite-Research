"""The agent engine shared by every workflow.

    openrouter  model client (retries, cache, cost)       runtime   role -> model + system prompt + hooks, Usage
    skills      SKILL.md files -> system prompt per role  hooks     checkpoints: trace, budget, quote check
    evidence    rows with page + quote, the quote check   agent     tool-calling loop
    decision    small model: accept / verify_more / revise learn     trace -> learned-rules skill
"""
