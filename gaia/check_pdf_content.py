#!/usr/bin/env python3
"""Check what was extracted from the PDF"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.pdf_loader import PDFLoader

loader = PDFLoader()
result = loader.extract('ui_components_spec.pdf')

print("=" * 80)
print("PDF Content Analysis")
print("=" * 80)
print(f"\nTotal characters: {len(result.text)}")
print(f"Total lines: {result.text.count(chr(10))}")
print(f"\nFirst 1000 characters:")
print("-" * 80)
print(result.text[:1000])
print("-" * 80)
print(f"\nLast 500 characters:")
print("-" * 80)
print(result.text[-500:])
print("-" * 80)

# Save to file for inspection
with open('pdf_extracted_text.txt', 'w', encoding='utf-8') as f:
    f.write(result.text)

print(f"\n✅ Full extracted text saved to: pdf_extracted_text.txt")
