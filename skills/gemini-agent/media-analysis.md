# Media Analysis

Gemini provides multi-modal understanding for images and documents.

## Image Analysis

### Basic Usage

```bash
gemini -p "Analyze this image @path/to/image.png"
```

### Common Patterns

```bash
# Screenshot analysis
gemini -p "@screenshot.png Describe the UI elements and layout"

# Diagram interpretation
gemini -p "@architecture.png Explain this system architecture diagram"

# Photo analysis
gemini -p "@photo.jpg What objects and text are visible in this image?"

# Error screenshot
gemini -p "@error.png What error is shown and how can I fix it?"
```

### Multiple Images

```bash
gemini -p "@before.png @after.png Compare these two screenshots and describe changes"
```

## Document Analysis

### Basic Usage

```bash
gemini -p "Analyze this document @path/to/document.pdf"
```

### Common Patterns

```bash
# PDF summary
gemini -p "@report.pdf Summarize the key findings in this document"

# Extract data
gemini -p "@invoice.pdf Extract all line items, amounts, and totals"

# Technical documentation
gemini -p "@spec.pdf What are the main requirements outlined here?"

# Contract review
gemini -p "@contract.pdf What are the key terms and obligations?"
```

### Large Documents

```bash
# Multi-page analysis
gemini -p "@manual.pdf Create a table of contents with page references"

# Specific section
gemini -p "@whitepaper.pdf Focus on the methodology section and explain the approach"
```

## Multi-Modal Prompts

Combine code, images, and documents in a single prompt:

```bash
# Code with documentation
gemini -p "@README.md @src/ Does the code match the documentation?"

# UI implementation verification
gemini -p "@design.png @src/components/ Does the implementation match this design?"

# Multiple document types
gemini -p "@requirements.pdf @src/ Are all requirements implemented?"
```

## Supported Formats

### Images
- PNG, JPG, JPEG, GIF, WebP
- Screenshots, diagrams, photos
- UI mockups and designs

### Documents
- PDF (multi-page supported)
- Text files (.txt, .md)
- Source code files (any language)

## Tips

1. **Be specific about what to extract**: "List all headings" vs "Summarize"
2. **Mention the content type**: "This is an architecture diagram" helps focus analysis
3. **Use relative paths**: Paths are relative to your current directory
4. **Combine with code**: Compare designs to implementations for verification
