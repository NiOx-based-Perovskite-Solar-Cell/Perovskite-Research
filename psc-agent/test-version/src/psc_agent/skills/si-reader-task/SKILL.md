---
name: si-reader-task
description: Read the text of one paper and say whether it refers to supporting information, quoting the sentence and the SI address when the paper prints one
roles: [si_reader]
order: 60
---
You read the text of one research paper (each page starts with `[page N]`) and decide whether the paper refers to
supporting information: material published apart from the paper, also called "supplementary information / materials /
data / files", "ESI" or "SI", with items such as "Figure S1" or "Table S2".

Answer from the paper only. Reply with JSON only: {"refers_to_si", "evidence", "address", "holds"}.

- refers_to_si: true when the paper points the reader to such material. A statement that no additional information
  exists, or that the data are available on request, is false. A reference to the SI of another paper does not count,
  nor does the unit "SI" (Système International).
- evidence: the one sentence that shows it, copied exactly as printed (empty when refers_to_si is false and nothing in
  the paper speaks about it). When the paper says there is none, copy that sentence.
- address: a web address for the supporting information that the paper itself prints, such as "Supplementary data to
  this article can be found online at https://...". The PDF's line breaks may leave spaces or hyphens inside an address:
  join them. Copy it exactly; never write an address from memory, and leave it empty when the paper prints none.
- holds: what the paper says the supporting information contains, in one short line (empty when none).
