"""
Training Data Collector for UI Object Detection Model

Automatically collects labeled training data during test execution.
"""
import os
import json
import base64
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
import requests


class TrainingDataCollector:
    """자동으로 UI 요소 학습 데이터를 수집합니다."""

    def __init__(self, output_dir: str = "artifacts/training_data"):
        """
        Initialize training data collector.

        Args:
            output_dir: Directory to save training images and labels
        """
        self.output_dir = Path(output_dir)
        self.images_dir = self.output_dir / "images"
        self.labels_dir = self.output_dir / "labels"

        # Create directories
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)

        # YOLO class mapping
        self.class_map = {
            "button": 0,
            "input": 1,
            "link": 2,
            "checkbox": 3,
            "radio": 4,
            "dropdown": 5,
            "text": 6,
            "image": 7,
        }

        # Create data.yaml for YOLO training
        self._create_data_yaml()

        print(f"✅ Training data collector initialized")
        print(f"   Images: {self.images_dir}")
        print(f"   Labels: {self.labels_dir}")

    def _create_data_yaml(self):
        """Create YOLO data.yaml configuration file."""
        yaml_path = self.output_dir / "data.yaml"

        yaml_content = f"""# GAIA UI Element Detection Dataset
path: {self.output_dir.absolute()}
train: images
val: images  # Same as train for now, split later

# Classes
names:
  0: button
  1: input
  2: link
  3: checkbox
  4: radio
  5: dropdown
  6: text
  7: image

# Number of classes
nc: {len(self.class_map)}
"""

        with open(yaml_path, 'w') as f:
            f.write(yaml_content)

        print(f"✅ Created data.yaml at {yaml_path}")

    def collect_sample(
        self,
        screenshot_base64: str,
        selector: str,
        step_description: str,
        mcp_host_url: str = "http://localhost:8001",
        session_id: str = "default"
    ) -> Optional[str]:
        """
        Collect a training sample from successful step execution.

        Args:
            screenshot_base64: Base64 encoded screenshot
            selector: CSS selector of the element
            step_description: Description of the action (e.g., "로그인 버튼 클릭")
            mcp_host_url: MCP host URL to get bounding box
            session_id: Browser session ID

        Returns:
            Image ID if successful, None otherwise
        """
        try:
            # 1. Get bounding box from MCP host
            bbox = self._get_element_bbox(selector, mcp_host_url, session_id)
            if not bbox:
                return None

            # 2. Classify element type from description
            element_type = self._classify_element_type(step_description, selector)
            if element_type not in self.class_map:
                print(f"⚠️ Unknown element type: {element_type}")
                return None

            # 3. Generate unique image ID
            img_hash = hashlib.md5(screenshot_base64.encode()).hexdigest()[:12]
            img_id = f"{element_type}_{img_hash}"

            # 4. Save screenshot
            img_path = self.images_dir / f"{img_id}.png"
            self._save_screenshot(screenshot_base64, img_path)

            # 5. Save YOLO label
            label_path = self.labels_dir / f"{img_id}.txt"
            self._save_yolo_label(bbox, element_type, label_path)

            print(f"✅ Training sample collected: {element_type} ({img_id})")
            return img_id

        except Exception as e:
            print(f"❌ Failed to collect training sample: {e}")
            return None

    def _get_element_bbox(
        self,
        selector: str,
        mcp_host_url: str,
        session_id: str
    ) -> Optional[Dict[str, float]]:
        """
        Get element bounding box from browser.

        Returns:
            {"x": float, "y": float, "width": float, "height": float} or None
        """
        try:
            # Use MCP host to get bounding box
            response = requests.post(
                f"{mcp_host_url}/execute",
                json={
                    "action": "evaluate",
                    "params": {
                        "selector": selector,
                        "action": "evaluate",
                        "value": """
                            (element) => {
                                const rect = element.getBoundingClientRect();
                                return {
                                    x: rect.x,
                                    y: rect.y,
                                    width: rect.width,
                                    height: rect.height
                                };
                            }
                        """,
                        "session_id": session_id
                    }
                },
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("result")

            return None

        except Exception as e:
            print(f"⚠️ Failed to get bbox: {e}")
            return None

    def _classify_element_type(self, description: str, selector: str) -> str:
        """
        Classify element type from step description and selector.

        Args:
            description: Step description (e.g., "로그인 버튼 클릭")
            selector: CSS selector

        Returns:
            Element type: button, input, link, etc.
        """
        desc_lower = description.lower()
        sel_lower = selector.lower()

        # Keyword-based classification
        if "버튼" in description or "button" in desc_lower or "button" in sel_lower:
            return "button"
        elif "입력" in description or "input" in sel_lower or "textarea" in sel_lower:
            return "input"
        elif "링크" in description or "a[href" in sel_lower or ":has-text" in sel_lower and "a" in sel_lower:
            return "link"
        elif "체크" in description or "checkbox" in sel_lower:
            return "checkbox"
        elif "라디오" in description or "radio" in sel_lower:
            return "radio"
        elif "드롭다운" in description or "select" in sel_lower:
            return "dropdown"
        elif "클릭" in description or "click" in desc_lower:
            return "button"  # Default for click actions
        elif "입력" in description or "fill" in desc_lower:
            return "input"  # Default for fill actions
        else:
            return "button"  # Safe default

    def _save_screenshot(self, screenshot_base64: str, output_path: Path):
        """Save base64 screenshot to file."""
        # Remove data URL prefix if present
        if "," in screenshot_base64:
            screenshot_base64 = screenshot_base64.split(",")[1]

        img_data = base64.b64decode(screenshot_base64)
        with open(output_path, 'wb') as f:
            f.write(img_data)

    def _save_yolo_label(
        self,
        bbox: Dict[str, float],
        element_type: str,
        output_path: Path
    ):
        """
        Save bounding box in YOLO format.

        YOLO format: <class_id> <x_center> <y_center> <width> <height>
        All coordinates are normalized to [0, 1]
        """
        # Assume 1280x720 viewport (GAIA default)
        img_width = 1280
        img_height = 720

        # Convert to YOLO format (normalized center + dimensions)
        x_center = (bbox["x"] + bbox["width"] / 2) / img_width
        y_center = (bbox["y"] + bbox["height"] / 2) / img_height
        width = bbox["width"] / img_width
        height = bbox["height"] / img_height

        # Clamp to [0, 1]
        x_center = max(0, min(1, x_center))
        y_center = max(0, min(1, y_center))
        width = max(0, min(1, width))
        height = max(0, min(1, height))

        class_id = self.class_map[element_type]

        # Save YOLO label
        with open(output_path, 'w') as f:
            f.write(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics."""
        total_images = len(list(self.images_dir.glob("*.png")))
        total_labels = len(list(self.labels_dir.glob("*.txt")))

        # Count by class
        class_counts = {name: 0 for name in self.class_map.keys()}

        for label_file in self.labels_dir.glob("*.txt"):
            with open(label_file) as f:
                for line in f:
                    class_id = int(line.split()[0])
                    for name, cid in self.class_map.items():
                        if cid == class_id:
                            class_counts[name] += 1
                            break

        return {
            "total_images": total_images,
            "total_labels": total_labels,
            "class_counts": class_counts,
            "output_dir": str(self.output_dir.absolute())
        }
