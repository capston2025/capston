#!/usr/bin/env python3
"""Convert UI components spec markdown to PDF with proper Korean font support"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def setup_korean_font():
    """Setup Korean font for reportlab"""
    try:
        # Try to register a Korean font (macOS system font)
        font_paths = [
            '/System/Library/Fonts/Supplemental/AppleGothic.ttf',  # macOS
            '/Library/Fonts/AppleGothic.ttf',
            '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',  # Linux
        ]

        for font_path in font_paths:
            if Path(font_path).exists():
                pdfmetrics.registerFont(TTFont('Korean', font_path))
                print(f"✅ Registered Korean font: {font_path}")
                return True

        print("⚠️  No Korean font found, using default")
        return False

    except Exception as e:
        print(f"⚠️  Font registration failed: {e}")
        return False

def markdown_to_pdf(md_path: Path, pdf_path: Path):
    """Convert markdown file to PDF with Korean support"""

    # Setup Korean font
    has_korean_font = setup_korean_font()

    # Read markdown
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Create PDF
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    # Get base styles
    styles = getSampleStyleSheet()

    # Create custom styles with Korean font
    if has_korean_font:
        title_style = ParagraphStyle(
            'KoreanTitle',
            parent=styles['Title'],
            fontName='Korean',
            fontSize=16,
            leading=20
        )
        h1_style = ParagraphStyle(
            'KoreanH1',
            parent=styles['Heading1'],
            fontName='Korean',
            fontSize=14,
            leading=18
        )
        h2_style = ParagraphStyle(
            'KoreanH2',
            parent=styles['Heading2'],
            fontName='Korean',
            fontSize=12,
            leading=16
        )
        normal_style = ParagraphStyle(
            'KoreanNormal',
            parent=styles['Normal'],
            fontName='Korean',
            fontSize=9,
            leading=12
        )
    else:
        title_style = styles['Title']
        h1_style = styles['Heading1']
        h2_style = styles['Heading2']
        normal_style = styles['Normal']

    # Build story
    story = []
    lines = content.split('\n')

    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            story.append(Spacer(1, 0.1*inch))
            continue

        # Escape special characters for reportlab
        line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # Handle headers
        if line.startswith('# '):
            text = line[2:].strip()
            story.append(Paragraph(text, title_style))
            story.append(Spacer(1, 0.2*inch))

        elif line.startswith('## '):
            text = line[3:].strip()
            story.append(Paragraph(text, h1_style))
            story.append(Spacer(1, 0.15*inch))

        elif line.startswith('### '):
            text = line[4:].strip()
            story.append(Paragraph(text, h2_style))
            story.append(Spacer(1, 0.1*inch))

        elif line.startswith('#### '):
            text = line[5:].strip()
            story.append(Paragraph(text, h2_style))
            story.append(Spacer(1, 0.1*inch))

        # Handle lists
        elif line.startswith('- ') or line.startswith('* '):
            text = line[2:].strip()
            story.append(Paragraph(f"• {text}", normal_style))

        elif line.startswith('---'):
            story.append(Spacer(1, 0.2*inch))

        # Handle numbered lists
        elif len(line) > 2 and line[0].isdigit() and line[1] == '.':
            text = line[2:].strip()
            story.append(Paragraph(text, normal_style))

        # Regular text
        else:
            # Skip markdown markers
            if line.startswith('```') or line.startswith('|'):
                continue

            # Limit line length to prevent overflow
            if len(line) > 500:
                line = line[:500] + '...'

            try:
                story.append(Paragraph(line, normal_style))
            except Exception as e:
                print(f"⚠️  Skipping line due to error: {str(e)[:50]}")
                continue

    # Build PDF
    print(f"📄 Generating PDF with {len(story)} elements...")
    try:
        doc.build(story)
        print(f"✅ PDF created: {pdf_path}")
        print(f"   File size: {pdf_path.stat().st_size / 1024:.1f} KB")
        return True
    except Exception as e:
        print(f"❌ Failed to build PDF: {e}")
        return False

if __name__ == "__main__":
    md_file = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec.md")
    pdf_file = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec_korean.pdf")

    print("=" * 60)
    print("Converting UI Components Spec to PDF (with Korean support)")
    print("=" * 60)
    print(f"📄 Source: {md_file}")
    print(f"📄 Target: {pdf_file}")
    print()

    try:
        success = markdown_to_pdf(md_file, pdf_file)
        if success:
            print("\n✅ Conversion complete!")
            print("\nNext steps:")
            print("1. Open GAIA GUI")
            print("2. Drag and drop ui_components_spec_korean.pdf")
            print("3. Watch Agent Builder generate comprehensive test cases")
        else:
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
