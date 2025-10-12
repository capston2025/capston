#!/usr/bin/env python3
"""Convert UI components spec markdown to PDF for testing"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def markdown_to_pdf(md_path: Path, pdf_path: Path):
    """Convert markdown file to PDF"""

    # Read markdown
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Create PDF
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=18,
    )

    # Use existing styles from reportlab
    styles = getSampleStyleSheet()

    # Build story
    story = []
    lines = content.split('\n')

    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            story.append(Spacer(1, 0.2*inch))
            continue

        # Handle headers
        if line.startswith('# '):
            text = line[2:].strip()
            story.append(Paragraph(text, styles['Title']))
            story.append(Spacer(1, 0.3*inch))

        elif line.startswith('## '):
            text = line[3:].strip()
            story.append(Paragraph(text, styles['Heading1']))
            story.append(Spacer(1, 0.2*inch))

        elif line.startswith('### '):
            text = line[4:].strip()
            story.append(Paragraph(text, styles['Heading2']))
            story.append(Spacer(1, 0.15*inch))

        elif line.startswith('#### '):
            text = line[5:].strip()
            story.append(Paragraph(text, styles['Heading3']))
            story.append(Spacer(1, 0.1*inch))

        # Handle lists
        elif line.startswith('- ') or line.startswith('* '):
            text = line[2:].strip()
            story.append(Paragraph(f"• {text}", styles['Normal']))

        elif line.startswith('---'):
            story.append(Spacer(1, 0.3*inch))

        # Handle numbered lists
        elif len(line) > 2 and line[0].isdigit() and line[1] == '.':
            text = line[2:].strip()
            story.append(Paragraph(text, styles['Normal']))

        # Regular text
        else:
            # Skip markdown markers
            if line.startswith('```') or line.startswith('|'):
                continue

            story.append(Paragraph(line, styles['Normal']))

    # Build PDF
    print(f"📄 Generating PDF...")
    doc.build(story)
    print(f"✅ PDF created: {pdf_path}")
    print(f"   File size: {pdf_path.stat().st_size / 1024:.1f} KB")

if __name__ == "__main__":
    md_file = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec.md")
    pdf_file = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec.pdf")

    print("=" * 60)
    print("Converting UI Components Spec to PDF")
    print("=" * 60)
    print(f"📄 Source: {md_file}")
    print(f"📄 Target: {pdf_file}")

    try:
        markdown_to_pdf(md_file, pdf_file)
        print("\n✅ Conversion complete!")
        print("\nNext steps:")
        print("1. Open GAIA GUI")
        print("2. Drag and drop ui_components_spec.pdf")
        print("3. Watch Agent Builder generate comprehensive test cases")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
