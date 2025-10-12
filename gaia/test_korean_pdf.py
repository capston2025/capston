#!/usr/bin/env python3
"""Test Korean PDF extraction"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.pdf_loader import PDFLoader

loader = PDFLoader()
result = loader.extract('ui_components_spec_korean.pdf')

print("=" * 80)
print("Korean PDF Extraction Test")
print("=" * 80)
print(f"\nTotal characters: {len(result.text)}")
print(f"\nFirst 500 characters:")
print("-" * 80)
print(result.text[:500])
print("-" * 80)

# Check if Korean characters are present
has_korean = any('\uac00' <= char <= '\ud7a3' for char in result.text[:1000])
has_black_squares = '■' in result.text[:1000]

print(f"\n✅ Has Korean characters: {has_korean}")
print(f"{'✅' if not has_black_squares else '❌'} Has black squares: {has_black_squares}")

if has_korean and not has_black_squares:
    print("\n🎉 PDF extraction successful! Korean text is readable.")
else:
    print("\n⚠️  PDF extraction may have issues.")
