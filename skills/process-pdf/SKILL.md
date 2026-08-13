---
name: process-pdf
description: Extract text, tables, form values, images, and merge PDFs. Use when working with PDF files, forms, or document extraction. Requires Python 3 and pdfplumber+pypdf (install via pip/uv).
---

# PDF Processing

## Quick start

Extract text:
```python
import pdfplumber
with pdfplumber.open("doc.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```

For reference on pdfplumber usage, see [reference.md](reference.md).

For reference on pypdf:
- Merging PDFs [merging-pdfs.md](merging-pdfs.md)
- Extracting images [extract-images.md](extract-images.md)

## Requirements

Packages must be installed in your environment (choose one):

```bash
python -m pip install pypdf pdfplumber

# or, if you use uv
uv pip install pypdf pdfplumber
```
