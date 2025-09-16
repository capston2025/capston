from google.cloud import vision
import io
import json
from PIL import Image, ImageDraw

class GoogleVisionTester:
    def __init__(self):
        """Google Vision API 클라이언트 초기화"""
        self.client = vision.ImageAnnotatorClient()
    
    def detect_text(self, image_path):
        """이미지에서 텍스트 탐지"""
        with io.open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = self.client.text_detection(image=image)
        texts = response.text_annotations
        
        if response.error.message:
            raise Exception(f'{response.error.message}')
        
        results = []
        for text in texts:
            vertices = [(vertex.x, vertex.y) for vertex in text.bounding_poly.vertices]
            center_x = sum(v[0] for v in vertices) // len(vertices)
            center_y = sum(v[1] for v in vertices) // len(vertices)
            
            results.append({
                'text': text.description,
                'x': center_x,
                'y': center_y,
                'bounding_box': vertices,
                'confidence': getattr(text, 'confidence', 0.9)
            })
        
        return results
    
    def detect_objects(self, image_path):
        """이미지에서 객체 탐지"""
        with io.open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = self.client.object_localization(image=image)
        objects = response.localized_object_annotations
        
        if response.error.message:
            raise Exception(f'{response.error.message}')
        
        results = []
        for obj in objects:
            vertices = [(vertex.x, vertex.y) for vertex in obj.bounding_poly.normalized_vertices]
            
            # 정규화된 좌표를 실제 픽셀로 변환 (이미지 크기 필요)
            img = Image.open(image_path)
            width, height = img.size
            
            pixel_vertices = [(int(v[0] * width), int(v[1] * height)) for v in vertices]
            center_x = sum(v[0] for v in pixel_vertices) // len(pixel_vertices)
            center_y = sum(v[1] for v in pixel_vertices) // len(pixel_vertices)
            
            results.append({
                'name': obj.name,
                'confidence': obj.score,
                'x': center_x,
                'y': center_y,
                'bounding_box': pixel_vertices
            })
        
        return results
    
    def analyze_ui_elements(self, image_path):
        """UI 요소 분석 (텍스트 + 객체 탐지 조합)"""
        print(f"분석 중: {image_path}")
        
        try:
            # 텍스트 탐지
            texts = self.detect_text(image_path)
            print(f"탐지된 텍스트: {len(texts)}개")
            
            # 객체 탐지
            objects = self.detect_objects(image_path)
            print(f"탐지된 객체: {len(objects)}개")
            
            # UI 요소로 분류
            ui_elements = []
            
            # 버튼으로 추정되는 텍스트들
            button_keywords = ['button', 'click', 'submit', '로그인', '회원가입', '확인', '취소', '검색']
            for text in texts:
                if any(keyword in text['text'].lower() for keyword in button_keywords):
                    ui_elements.append({
                        'type': 'button',
                        'text': text['text'],
                        'x': text['x'],
                        'y': text['y'],
                        'confidence': text['confidence']
                    })
            
            return {
                'ui_elements': ui_elements,
                'all_texts': texts,
                'all_objects': objects
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def visualize_results(self, image_path, results):
        """결과를 이미지에 시각화"""
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        
        # UI 요소들을 빨간 박스로 표시
        for element in results.get('ui_elements', []):
            x, y = element['x'], element['y']
            draw.rectangle([x-20, y-10, x+20, y+10], outline='red', width=2)
            draw.text((x, y-25), element['text'], fill='red')
        
        # 결과 이미지 저장
        output_path = image_path.replace('.', '_analyzed.')
        img.save(output_path)
        print(f"분석 결과 저장: {output_path}")
        
        return output_path

def main():
    """메인 테스트 함수"""
    print("=== Google Vision API UI 탐지 테스트 ===")
    
    tester = GoogleVisionTester()
    
    # 테스트 이미지 경로 (사용자가 제공해야 함)
    test_image = "screenshot.png"  # 실제 스크린샷 파일명으로 변경
    
    print(f"테스트 이미지: {test_image}")
    print("주의: 실제 스크린샷 파일이 필요합니다!")
    
    try:
        results = tester.analyze_ui_elements(test_image)
        
        if 'error' in results:
            print(f"오류: {results['error']}")
        else:
            print("\n=== 분석 결과 ===")
            print(json.dumps(results, ensure_ascii=False, indent=2))
            
            # 시각화
            tester.visualize_results(test_image, results)
            
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {test_image}")
        print("실제 웹페이지 스크린샷을 screenshot.png로 저장하고 다시 실행하세요.")
    except Exception as e:
        print(f"API 연결 오류: {e}")
        print("Google Cloud 설정을 확인하세요.")

if __name__ == "__main__":
    main()