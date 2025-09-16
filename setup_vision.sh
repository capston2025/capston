#!/bin/bash

# Google Cloud Vision API 설정 스크립트

echo "=== Google Cloud Vision API 설정 ==="

# 방법 1: 서비스 계정 키 파일 사용
echo "방법 1: 서비스 계정 키 파일 설정"
echo "export GOOGLE_APPLICATION_CREDENTIALS='path/to/your/keyfile.json'"
echo ""

# 방법 2: gcloud CLI 인증 (추천)
echo "방법 2: gcloud CLI 인증 (추천)"
echo "다음 명령어들을 실행하세요:"
echo ""
echo "# gcloud CLI 설치 (Homebrew)"
echo "brew install google-cloud-sdk"
echo ""
echo "# 인증"
echo "gcloud auth application-default login"
echo ""
echo "# 프로젝트 설정"
echo "gcloud config set project YOUR_PROJECT_ID"
echo ""

# 방법 3: API 키 사용 (제한적)
echo "방법 3: API 키 사용"
echo "주의: Vision API는 서비스 계정 인증이 필요할 수 있습니다."
echo ""

echo "설정 완료 후 다음 명령어로 테스트:"
echo "python google_vision_test.py"