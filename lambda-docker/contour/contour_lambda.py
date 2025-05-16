import cv2
import numpy as np
import base64
import json
import os
import tempfile

# Thresholds and constants
LAB_TOLERANCE = 30
HSV_TOLERANCE = (20, 60, 60)
MORPH_KERNEL = np.ones((7, 7), np.uint8)

def filter_outliers(region):
    region_reshaped = region.reshape(-1, 3)
    mean_color = np.mean(region_reshaped, axis=0)
    diff = np.linalg.norm(region_reshaped - mean_color, axis=1)
    filtered_pixels = region_reshaped[diff < np.std(diff) * 2]
    return np.mean(filtered_pixels, axis=0) if filtered_pixels.size > 0 else mean_color


def normalize_lab(image):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    return cv2.merge((l, a, b))


def apply_line_filter(mask, left_line, right_line):
    left = min(left_line, right_line)
    right = max(left_line, right_line)
    line_mask = np.zeros(mask.shape, dtype=np.uint8)
    line_mask[:, left:right] = 255
    return cv2.bitwise_and(mask, line_mask)


def lambda_handler(event, context=None):
    try:
        # Parse body for API Gateway
        if 'body' in event and isinstance(event['body'], str):
            body = json.loads(event['body'])
        else:
            body = event

        # Decode image
        image_data = base64.b64decode(body['imageBase64'])
        nparr = np.frombuffer(image_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # Extract parameters
        reference_color_hsv = np.array(body['reference_color_hsv'], dtype=np.uint8)
        left_line = int(body['left_line'])
        right_line = int(body['right_line'])
        min_area = int(body.get('min_area', 100))
        max_area = int(body.get('max_area', 5000))

        # Preprocess images
        lab_image = normalize_lab(image)
        hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        # Convert reference HSV to LAB via BGR
        hsv_pixel = np.uint8([[[reference_color_hsv[0], reference_color_hsv[1], reference_color_hsv[2]]]])
        bgr_pixel = cv2.cvtColor(hsv_pixel, cv2.COLOR_HSV2BGR)
        lab_pixel = cv2.cvtColor(bgr_pixel, cv2.COLOR_BGR2LAB)[0][0]

        # Build LAB mask
        diff_lab = np.linalg.norm(lab_image - lab_pixel, axis=2)
        mask_lab = (diff_lab < LAB_TOLERANCE).astype(np.uint8) * 255

        # Build HSV mask
        hue_diff = np.abs(hsv_image[:, :, 0].astype(int) - int(reference_color_hsv[0]))
        hue_diff = np.minimum(hue_diff, 180 - hue_diff)
        diff_s = np.abs(hsv_image[:, :, 1].astype(int) - int(reference_color_hsv[1]))
        diff_v = np.abs(hsv_image[:, :, 2].astype(int) - int(reference_color_hsv[2]))
        mask_hsv = ((hue_diff < HSV_TOLERANCE[0]) &
                    (diff_s < HSV_TOLERANCE[1]) &
                    (diff_v < HSV_TOLERANCE[2])
                   ).astype(np.uint8) * 255

        # Combine & filter
        mask = cv2.bitwise_and(mask_lab, mask_hsv)
        mask = apply_line_filter(mask, left_line, right_line)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, MORPH_KERNEL)
        mask = cv2.dilate(mask, MORPH_KERNEL, iterations=3)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        metadata = {}
        original_image_width = image.shape[1]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, contour in enumerate(contours):
                area = cv2.contourArea(contour)
                if area < min_area or area > max_area:
                    continue

                (x, y), _ = cv2.minEnclosingCircle(contour)
                center = (int(x), int(y))
                rect = cv2.minAreaRect(contour)
                box = cv2.boxPoints(rect).astype(int)
                x_min, y_min = np.min(box, axis=0)
                x_max, y_max = np.max(box, axis=0)

                # Crop and save
                crop_img = image[y_min:y_max, x_min:x_max]
                filename = f"contour_{i}_x{center[0]}_y{center[1]}.jpg"
                cv2.imwrite(os.path.join(tmpdir, filename), crop_img)

                # Compute hold color HSV
                hsv_mask = np.zeros(image.shape[:2], dtype=np.uint8)
                cv2.drawContours(hsv_mask, [contour], -1, 255, -1)
                hsv_pixels = hsv_image[hsv_mask == 255]
                hold_color_hsv = np.mean(hsv_pixels, axis=0).tolist() if len(hsv_pixels) else [0, 0, 0]

                zoom_factor = (x_max - x_min) / original_image_width
                metadata[filename] = {
                    "center": center,
                    "bounding_box": [(int(x_min), int(y_min)), (int(x_max), int(y_max))],
                    "zoom_factor": zoom_factor,
                    "color_hsv": hold_color_hsv
                }

        # Successful response with CORS header
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "message": "Contours extracted successfully",
                "contour_metadata": metadata,
                "contour_count": len(metadata)
            })
        }

    except Exception as e:
        print("Exception in contour_lambda:", e, flush=True)
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"error": str(e)})
        }
