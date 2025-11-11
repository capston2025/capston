"""
UI Element Detector using YOLO

Vision-based element detection as fallback when DOM-based methods fail.
"""
import os
import base64
import io
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

import numpy as np
from PIL import Image


class UIDetector:
    """
    YOLO-based UI element detector.

    Used as fallback when DOM-based selector finding fails.
    """

    def __init__(self, model_path: str = "gaia/models/ui_detector.pt"):
        """
        Initialize UI detector.

        Args:
            model_path: Path to trained YOLO model
        """
        self.model_path = Path(model_path)
        self.model = None
        self.enabled = False

        # Lazy load model (only when needed)
        if self.model_path.exists():
            try:
                from ultralytics import YOLO
                self.model = YOLO(str(self.model_path))
                self.enabled = True
                print(f"✅ UI Detector loaded: {model_path}")
            except ImportError:
                print("⚠️ ultralytics not installed, YOLO detector disabled")
                print("   Install: pip install ultralytics")
            except Exception as e:
                print(f"⚠️ Failed to load UI Detector: {e}")
        else:
            print(f"⚠️ UI Detector model not found: {model_path}")
            print("   Train model first: python train_local.py")

        # Class names (must match training data)
        self.class_names = {
            0: "button",
            1: "input",
            2: "link",
            3: "checkbox",
            4: "radio",
            5: "dropdown",
            6: "text",
            7: "image"
        }

    def _decode_screenshot(self, screenshot_base64: str) -> np.ndarray:
        """Decode base64 screenshot to numpy array."""
        # Remove data URL prefix if present
        if "," in screenshot_base64:
            screenshot_base64 = screenshot_base64.split(",")[1]

        img_data = base64.b64decode(screenshot_base64)
        img = Image.open(io.BytesIO(img_data))
        return np.array(img)

    def detect_all_elements(
        self,
        screenshot_base64: str,
        confidence_threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Detect all UI elements in screenshot.

        Args:
            screenshot_base64: Base64 encoded screenshot
            confidence_threshold: Minimum confidence (0-1)

        Returns:
            List of detected elements:
            [
                {
                    "type": "button",
                    "confidence": 0.95,
                    "bbox": {"x1": 100, "y1": 200, "x2": 150, "y2": 230},
                    "center": {"x": 125, "y": 215}
                },
                ...
            ]
        """
        if not self.enabled:
            return []

        try:
            # Decode image
            img = self._decode_screenshot(screenshot_base64)

            # Run YOLO detection
            results = self.model(img, conf=confidence_threshold, verbose=False)

            # Parse results
            elements = []
            for box in results[0].boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()

                elements.append({
                    "type": self.class_names.get(class_id, "unknown"),
                    "confidence": confidence,
                    "bbox": {
                        "x1": int(x1),
                        "y1": int(y1),
                        "x2": int(x2),
                        "y2": int(y2)
                    },
                    "center": {
                        "x": int((x1 + x2) / 2),
                        "y": int((y1 + y2) / 2)
                    }
                })

            return elements

        except Exception as e:
            print(f"⚠️ YOLO detection failed: {e}")
            return []

    def find_element_by_type(
        self,
        screenshot_base64: str,
        element_type: str,
        step_description: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Find UI element by type.

        Args:
            screenshot_base64: Base64 encoded screenshot
            element_type: "button", "input", "link", etc.
            step_description: Step description for better matching

        Returns:
            Element with highest confidence, or None
        """
        elements = self.detect_all_elements(screenshot_base64)

        # Filter by type
        matching = [e for e in elements if e["type"] == element_type]

        if not matching:
            return None

        # Return highest confidence
        return max(matching, key=lambda e: e["confidence"])

    def find_element_coordinates(
        self,
        screenshot_base64: str,
        step_description: str
    ) -> Optional[Tuple[int, int]]:
        """
        Find element coordinates from step description.

        This is the main method used as fallback in intelligent_orchestrator.

        Args:
            screenshot_base64: Base64 encoded screenshot
            step_description: Step description (e.g., "로그인 버튼 클릭")

        Returns:
            (x, y) coordinates or None
        """
        if not self.enabled:
            return None

        # Classify element type from description
        element_type = self._classify_element_type(step_description)

        # Find by type
        result = self.find_element_by_type(
            screenshot_base64,
            element_type,
            step_description
        )

        if result:
            return (result["center"]["x"], result["center"]["y"])

        return None

    def _classify_element_type(self, description: str) -> str:
        """
        Classify element type from step description.

        Args:
            description: Step description (e.g., "로그인 버튼 클릭")

        Returns:
            Element type: "button", "input", "link", etc.
        """
        desc_lower = description.lower()

        # Keyword-based classification
        if "버튼" in description or "button" in desc_lower or "클릭" in description:
            return "button"
        elif "입력" in description or "input" in desc_lower or "fill" in desc_lower:
            return "input"
        elif "링크" in description or "link" in desc_lower:
            return "link"
        elif "체크" in description or "checkbox" in desc_lower:
            return "checkbox"
        elif "라디오" in description or "radio" in desc_lower:
            return "radio"
        elif "드롭다운" in description or "select" in desc_lower or "dropdown" in desc_lower:
            return "dropdown"
        else:
            return "button"  # Safe default

    def match_with_dom(
        self,
        screenshot_base64: str,
        dom_elements: List[Any]
    ) -> List[Dict[str, Any]]:
        """
        Match YOLO detections with DOM elements.

        This creates a hybrid: YOLO finds visual location, DOM provides selector.

        Args:
            screenshot_base64: Base64 encoded screenshot
            dom_elements: List of DomElement objects

        Returns:
            List of matched elements with both YOLO and DOM info
        """
        yolo_elements = self.detect_all_elements(screenshot_base64)
        matched = []

        for yolo_elem in yolo_elements:
            center_x = yolo_elem["center"]["x"]
            center_y = yolo_elem["center"]["y"]

            # Find DOM element at this position
            # Note: This requires getting bounding boxes from DOM elements
            # which needs MCP host support
            for dom_elem in dom_elements:
                # TODO: Get DOM element bounding box and check overlap
                # For now, just match by type
                if self._type_matches(yolo_elem["type"], dom_elem.tag):
                    matched.append({
                        "yolo": yolo_elem,
                        "dom": dom_elem,
                        "selector": dom_elem.selector,
                        "center": yolo_elem["center"]
                    })
                    break

        return matched

    def _type_matches(self, yolo_type: str, dom_tag: str) -> bool:
        """Check if YOLO type matches DOM tag."""
        mapping = {
            "button": ["button"],
            "input": ["input", "textarea"],
            "link": ["a"],
            "checkbox": ["input"],
            "radio": ["input"],
            "dropdown": ["select"]
        }

        return dom_tag in mapping.get(yolo_type, [])


# Global instance (lazy loaded)
_detector_instance = None


def get_ui_detector() -> UIDetector:
    """Get global UI detector instance."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = UIDetector()
    return _detector_instance
