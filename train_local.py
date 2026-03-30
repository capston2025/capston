#!/usr/bin/env python3
"""
GAIA UI Detector Training Script - MacBook Apple Silicon Optimized

맥북에서 바로 실행 가능한 간단한 학습 스크립트입니다.

사용법:
    python train_local.py

환경:
    - Apple Silicon (M1/M2/M3) 추천
    - 또는 NVIDIA GPU
    - 또는 CPU (느림)
"""
import os
import sys
import torch
from pathlib import Path
from ultralytics import YOLO


def main():
    print("🚀 GAIA UI Detector Training")
    print("=" * 60)

    # 1. 디바이스 확인
    if torch.backends.mps.is_available():
        device = "mps"
        print("✅ Using Apple Silicon GPU (MPS)")
        print(f"   PyTorch version: {torch.__version__}")
    elif torch.cuda.is_available():
        device = "0"
        print(f"✅ Using NVIDIA GPU: {torch.cuda.get_device_name(0)}")
    else:
        device = "cpu"
        print("⚠️  Using CPU (will be slow, 5-10 hours)")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            sys.exit(0)

    # 2. 데이터셋 확인
    data_dir = Path("artifacts/training_data")
    images_dir = data_dir / "images"
    labels_dir = data_dir / "labels"

    if not images_dir.exists():
        print(f"❌ Error: {images_dir} not found!")
        print("\n📝 To collect training data:")
        print("   1. Set environment variable: export GAIA_COLLECT_TRAINING_DATA=true")
        print("   2. Run tests normally: python run_auto_test.py --url ...")
        print("   3. Data will be collected automatically")
        sys.exit(1)

    num_images = len(list(images_dir.glob("*.png")))
    num_labels = len(list(labels_dir.glob("*.txt")))

    print(f"\n📊 Dataset Statistics:")
    print(f"   Images: {num_images}")
    print(f"   Labels: {num_labels}")

    if num_images < 50:
        print("⚠️  WARNING: Less than 50 images!")
        print("   Recommendation: Collect at least 100-200 images for good results")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            sys.exit(0)
    elif num_images < 200:
        print("⚠️  50-200 images: Model will work but accuracy may be limited")
    else:
        print("✅ Good dataset size!")

    # 3. 학습 파라미터
    MODEL_SIZE = "yolov8n.pt"  # nano - 맥북 최적
    EPOCHS = 50 if device == "mps" else 30  # 맥북: 50, CPU: 30
    BATCH_SIZE = 16 if device != "cpu" else 8
    IMG_SIZE = 640

    print(f"\n⚙️  Training Configuration:")
    print(f"   Model: {MODEL_SIZE} (nano - fastest)")
    print(f"   Epochs: {EPOCHS}")
    print(f"   Batch size: {BATCH_SIZE}")
    print(f"   Image size: {IMG_SIZE}x{IMG_SIZE}")

    if device == "mps":
        expected_time = "1-2 hours"
    elif device == "0":
        expected_time = "1-1.5 hours"
    else:
        expected_time = "5-10 hours"

    print(f"   Expected time: {expected_time}")

    # 4. 확인
    print("\n" + "=" * 60)
    print("⚡ Ready to start training!")
    print("   This will:")
    print("   - Split data into train/val (80/20)")
    print("   - Train YOLOv8n model")
    print("   - Save best model to models/ui_detector.pt")
    print("=" * 60)

    response = input("\nStart training? (Y/n): ")
    if response.lower() == 'n':
        print("❌ Training cancelled")
        sys.exit(0)

    # 5. 모델 로드
    print(f"\n📦 Loading {MODEL_SIZE}...")
    model = YOLO(MODEL_SIZE)

    # 6. data.yaml 확인
    yaml_path = data_dir / "data.yaml"
    if not yaml_path.exists():
        print(f"❌ Error: {yaml_path} not found!")
        print("   Prepare a YOLO dataset with images/, labels/, and data.yaml first")
        sys.exit(1)

    # 7. 학습 시작
    print(f"\n🚀 Training started...")
    print("=" * 60)

    try:
        results = model.train(
            data=str(yaml_path),
            epochs=EPOCHS,
            imgsz=IMG_SIZE,
            batch=BATCH_SIZE,
            device=device,

            # 최적화
            workers=4,
            cache=True,
            amp=True,

            # 저장
            project='runs/detect',
            name='ui_detector',
            exist_ok=True,

            # 조기 종료
            patience=10,

            # Verbose
            verbose=True,
            plots=True
        )

        print("\n" + "=" * 60)
        print("✅ Training completed successfully!")
        print("=" * 60)

        # 8. 모델 저장
        best_model = Path(results.save_dir) / "weights" / "best.pt"
        output_dir = Path("gaia/models")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ui_detector.pt"

        import shutil
        shutil.copy(best_model, output_path)

        print(f"\n💾 Model saved to: {output_path}")

        # 9. 평가
        print(f"\n📊 Evaluating model...")
        metrics = model.val()

        print(f"\n✅ Validation Results:")
        print(f"   mAP50: {metrics.box.map50:.3f}")
        print(f"   mAP50-95: {metrics.box.map:.3f}")
        print(f"   Precision: {metrics.box.mp:.3f}")
        print(f"   Recall: {metrics.box.mr:.3f}")

        if metrics.box.map50 > 0.7:
            print("\n🎉 Excellent! Model is ready to use.")
        elif metrics.box.map50 > 0.5:
            print("\n✅ Good! Model should work well.")
        else:
            print("\n⚠️  Model accuracy is low. Consider:")
            print("   - Collecting more training data")
            print("   - Training for more epochs")
            print("   - Using a larger model (yolov8s.pt)")

        # 10. 다음 단계
        print("\n" + "=" * 60)
        print("🎯 Next Steps:")
        print("=" * 60)
        print("1. Export the model artifact:")
        print("   Model is already saved to gaia/models/ui_detector.pt")
        print("\n2. Evaluate it with your own detection/inference script")
        print("   against a held-out validation set before wiring it back in")
        print("\n3. Collect more labeled data if accuracy is low")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n❌ Training interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
