import cv2
import numpy as np
import json
import base64
import tempfile

# Conversion & expected ranges
PIXEL_TO_CM = 50.0 / 272.0           # pixels → centimeters
EXPECTED_AREA_RANGE = (6.76, 169.0)  # area in cm² you expect holds to occupy

def extract_hold_features(contour, area_cm2):
    perimeter = cv2.arcLength(contour, True)
    circularity = 4 * np.pi * area_cm2 / (perimeter ** 2) if perimeter > 0 else 0
    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    convexity = area_cm2 / hull_area if hull_area > 0 else 0
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    aspect_ratio = max(w / h, h / w) if w > 0 and h > 0 else 1
    return circularity, convexity, aspect_ratio

def classify_hold(area, circularity, convexity, aspect_ratio):
    SIZE_THRESHOLD = 15  # cm²
    score = 0
    score += -1 if area >= SIZE_THRESHOLD else 2
    score += 1 if circularity > 0.5 else 0
    score += 1 if convexity < 0.7 else 0
    score += 1 if 0.7 <= aspect_ratio <= 1.3 else 0
    return "foothold" if score >= 2 else "handhold"

def compute_hold_grade(area, circularity, convexity, aspect_ratio, hold_type):
    min_a, max_a = EXPECTED_AREA_RANGE
    # normalize area into a 0–6 scale
    area_score = max(0, min(6, (max_a - area) / (max_a - min_a) * 8))
    circ_score = (1 - circularity) * 3
    conv_score = (1 - convexity) * 3
    ar_score   = abs(aspect_ratio - 1) * 2
    modifier   = 1.5 if hold_type == "foothold" else 1.2
    total = (area_score + circ_score + conv_score + ar_score) * modifier
    return int(round(max(1, min(10, total))))

def lambda_handler(event, context=None):
    try:
        # parse API Gateway payload
        if 'body' in event and isinstance(event['body'], str):
            body = json.loads(event['body'])
        else:
            body = event

        metadata = body.get('metadata', {})
        images   = body.get('images', {})
        updated  = {}

        for fname, data in metadata.items():
            img_b64 = images.get(fname)
            if not img_b64:
                continue

            # decode crop image
            img_bytes = base64.b64decode(img_b64)
            arr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

            # contour detection
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
            cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)

            # convert area to cm²
            pixel_area = cv2.contourArea(c)
            area_cm2 = pixel_area * (PIXEL_TO_CM**2)

            circ, conv, ar = extract_hold_features(c, area_cm2)
            htype = classify_hold(area_cm2, circ, conv, ar)
            grade = compute_hold_grade(area_cm2, circ, conv, ar, htype)

            entry = data.copy()
            entry['hold_type']  = htype
            entry['hold_grade'] = grade
            updated[fname] = entry

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': 'Holds graded successfully',
                'updated_metadata': updated
            })
        }

    except Exception as e:
        print('Exception in grade_hold_lambda:', e, flush=True)
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
