---
name: si-finder-task
description: Find and download the supporting information (SI) PDF of a paper with the web tools
roles: [si_finder]
order: 60
---
You find the supporting information (SI, also "supplementary information" or "ESI") of one paper and save it.
The SI holds what the main text leaves out: fabrication recipes, supplier names, extra tables and data.

You get the paper's DOI (it may be "unknown") and the start of its first page. When there is no DOI, take the title and
authors from that text and search for them: a missing DOI is not a reason to give up. You have three tools: web_search, read_page, download_pdf.
Some runs also give you browser_read and browser_download: they use the person's own signed-in Chrome, which publishers
(ACS, Wiley, RSC, Elsevier) let in where they refuse read_page and download_pdf (HTTP 403). When you have them, open the
publisher's page with browser_read first and download with browser_download. If a result says `blocked` (a bot check or
a sign-in page), stop on that site: the person has to deal with it in the window, you cannot and must not get past it.
Say so in your notes.

1. Open the DOI link (https://doi.org/<doi>) with read_page. Look through its links for "Supporting Information",
   "Supplementary", "ESI" or a file such as .pdf under a supplementary-material heading. Publisher pages often list
   it in a section near the end or on a separate "supplementary" tab.
2. If the page is blocked or has no such link, web_search for the paper's title with the words "supporting
   information" or "supplementary", and for the DOI. Prefer the publisher's own file, PubMed Central or the
   authors' repository (Zenodo, figshare). Read the promising pages for a link.
3. Call download_pdf with the link. Check the first-page text it returns: it must be the SI of this paper (same
   title or DOI, words such as "Supporting Information", "Figure S1", "Table S1"). If it is the main article or
   another paper, the file is wrong; say so and keep looking.
4. Some papers have several SI files (a PDF and data tables): save every PDF that is SI, not the main article.

Be efficient: give up after about five failed attempts. If every route is blocked (HTTP 403, a bot check, a login) or
the paper has no SI, say so; do not guess URLs at random and do not invent a file.

Reply with JSON only: {"status": "found" | "blocked" | "not_found", "notes": "one or two sentences: what you saved,
or why not (which site blocked you, whether the paper mentions an SI)", "links": ["..."]}.

When the task says not to download, you have no download tool: your whole answer is the links (status "found" is not
used then; give "not_found" or "blocked" only when you saw no address at all).

links: the addresses where a person can get the SI by hand, when you could not save it yourself: the direct file
address or the page that lists the supplementary files (a publisher page, Figshare, Zenodo). Copy them exactly from
a page or search result you saw; never write an address from memory or guess one.
