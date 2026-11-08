---
name: evidence-rules
description: Every value needs page + verbatim quote (or figure/table label); quotes are machine-checked
roles: [recheck, mapper, extract, decision, auditor]
order: 30
---
- Use only the paper you are given (text layer + page images). No outside knowledge, no typical values, no values
  of another device unless the paper says they are the same.
- Every value needs evidence: `page` = 1-based page number; `evidence` = a short quote (at most 25 words)
  copied exactly from that page's text, or a figure/table label such as "Fig. 3b" or "Table 1" when the value is
  read from a figure or table; `source` = text | table | figure | none.
- Quotes are checked automatically against the page text. A quote that is not on the cited page makes the value
  untrusted, so copy, do not paraphrase.
- Not reported → null. If the paper points to the Supporting Information (not available), use null and write
  "SI" in note. Never guess.
