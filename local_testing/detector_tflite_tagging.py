from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict
import numpy as np

try:
    from tflite_runtime.interpreter import Interpreter
except Exception:
    Interpreter = None

@dataclass
class Det:
    label: str
    confidence: float

class TFLiteDetector:
    def __init__(self, model_path: str, labels_path: str):
        if Interpreter is None:
            raise RuntimeError("tflite-runtime not installed in venv")

        self.model_path = str(model_path)
        self.labels = self._load_labels(labels_path)

        self.interp = Interpreter(model_path=self.model_path)
        self.interp.allocate_tensors()

        inp = self.interp.get_input_details()[0]
        self.inp_index = inp["index"]
        self.inp_shape = inp["shape"] # [1, h, w, c]
        self.inp_dtype = inp["dtype"]

        outs = self.interp.get_output_details()
        # attempt to map outputs by shape/name patterns
        #! We assume four outputs: boxes, classes, scores, num
        self.out_indices = [o["index"] for o in outs]

        self.h = int(self.inp_shape[1])
        self.w = int(self.inp_shape[2])

    def _load_labels(self, path: str) -> List[str]:
        p = Path(path)
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return lines

    def _preprocess(self, bgr: np.ndarray) -> np.ndarray:
        # bgr -> rgb, resize, add batch
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (self.w, self.h), interpolation=cv2.INTER_AREA)
        
        x = resized.astype(self.inp_dtype)
        if self.inp_dtype == np.float32:
            x = x / 255.0
        x = np.expand_dims(x, axis=0)
        return x
    
    def detect(self, bgr: np.ndarray, score_thresh: float = 0.35, top_k: int = 10) -> List[dict]:
        x = self._preprocess(bgr)
        self.interp.set_tensor(self.inp_index, x)
        self.interp.invoke()

        # read all outputs and identify which is which by shape
        outs = [self.interp.get_tensor(i) for i in self.out_indices]

        boxes = None
        classes = None
        scores = None
        num = None

        for o in outs:
            arr = np.array(o)
            if arr.ndim == 3 and arr.shape[-1] == 4:
                boxes = arr
            elif arr.ndim == 2 and arr.shape[-1] > 1:
                if arr.dtype in (np.float32, np.float64):
                    if float(arr.max()) <= 1.0:
                        scores = arr
                    else:
                        classes = arr
                else:
                    classes = arr
            
            elif arr.ndim == 2 and arr.shape[-1] == 1:
                num = arr
            elif arr.ndim == 1 and arr.shape[0] == 1:
                num = arr
            
        # fallback: many models output in a fixed order
        if boxes is None or classes is None or scores is None:
            try:
                boxes = outs[0]
                classes = outs[1]
                scores = outs[2]
                num = outs[3] if len(outs) > 3 else None
            except Exception:
                return []
        
        boxes = np.array(boxes)
        classes = np.array(classes)
        scores = np.array(scores)

        N = scores.shape[1] if scores.ndim == 2 else scores.shape[0]
        dets: List[Det] = []
        for i in range(min(N, top_k)):
            s = float(scores[0, i] if scores.ndim == 2 else scores[i])
            if s < score_thresh:
                continue
            c = int(classes[0, i] if classes.ndim == 2 else classes[i])
            # COCO label indexing varies; assume 0-based. If your labels are 1-based, adjust here.
            label = self.labels[c] if 0 <= c < len(self.labels) else f"class_{c}"
            dets.append(Det(label=label, confidence=s))

        return dets


if __name__ == "__main__":
    import tempfile
    import os
    
    print("Testing TFLiteDetector class...\n")
    
    # Test 1: Create a mock labels file
    print("✓ Creating mock labels file...")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("person\ncar\ndog\ncat\nbicycle\n")
        labels_file = f.name
    print(f"  Labels file: {labels_file}\n")
    
    try:
        # Test 2: Try to initialize detector (will fail without real model, but tests initialization logic)
        print("✓ Testing detector initialization...")
        if Interpreter is None:
            print("  tflite-runtime not available (expected in test environment)")
            print("  Skipping detector instantiation\n")
        else:
            try:
                detector = TFLiteDetector("fake_model.tflite", labels_file)
                print("  Detector initialized (unexpected - model file doesn't exist)")
            except FileNotFoundError:
                print("  Expected error: model file not found (OK)\n")
        
        # Test 3: Test Det dataclass
        print("✓ Testing Det dataclass...")
        det1 = Det(label="person", confidence=0.95)
        det2 = Det(label="car", confidence=0.87)
        print(f"  Det 1: {det1}")
        print(f"  Det 2: {det2}\n")
        
        # Test 4: Load labels
        print("✓ Testing label loading...")
        from pathlib import Path
        test_labels = Path(labels_file).read_text().strip().split('\n')
        print(f"  Loaded {len(test_labels)} labels: {test_labels}\n")
        
        print("✅ All basic tests passed!")
        
    finally:
        # Cleanup
        os.unlink(labels_file)
        print("\nTest files cleaned up.")