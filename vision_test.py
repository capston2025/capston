import google.generativeai as genai
from PIL import Image
import json

# API 키 설정
genai.configure(api_key="")
model = genai.GenerativeModel('')

def test_ui_detection(image_path):
    """웹페이지 스크린샷에서 UI 요소를 탐지하는 테스트 함수"""
    try:
        img = Image.open(image_path)
        
        prompt = """
        이 웹페이지 스크린샷에서 모든 클릭 가능한 UI 요소들을 찾아주세요.
        각 요소에 대해 다음 정보를 JSON 배열로 반환해주세요:
        
        [
          {
            "type": "요소 종류 (button, link, input, dropdown 등)",
            "text": "보이는 텍스트 또는 라벨",
            "x": 중심 x좌표(픽셀),
            "y": 중심 y좌표(픽셀), 
            "confidence": 확신도(0-1 사이 소수)
          }
        ]
        
        오직 JSON 형태로만 응답해주세요.
        """
        
        response = model.generate_content([prompt, img])
        return response.text
        
    except Exception as e:
        return f"오류 발생: {str(e)}"

def simple_test():
    """간단한 텍스트 인식 테스트"""
    try:
        # 텍스트만으로 테스트
        response = model.generate_content("안녕하세요! API가 정상 작동하나요?")
        return response.text
    except Exception as e:
        return f"API 연결 오류: {str(e)}"

if __name__ == "__main__":
    # 먼저 API 연결 테스트
    print("=== API 연결 테스트 ===")
    result = simple_test()
    print(result)
    print()
    
    # 이미지 테스트 (이미지 파일이 있을 때)
    print("=== UI 탐지 테스트 ===")
    print("사용법: test_ui_detection('스크린샷.png')")
    print("예시 이미지가 있다면 아래 주석을 해제하고 실행하세요:")
    print("# result = test_ui_detection('screenshot.png')")
    print("# print(result)")