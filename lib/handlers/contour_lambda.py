from requests_toolbelt.multipart import decoder
import base64
import cv2
import numpy as np
import json
import os
import tempfile

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
        # Debug: Print incoming event for CloudWatch
        print("=== EVENT HEADERS ===")
        print(json.dumps(event.get('headers', {})))
        print("=== EVENT isBase64Encoded ===")
        print(event.get('isBase64Encoded', False))
        print("=== EVENT BODY LEN ===")
        print(len(event.get('body', b'')))

        headers = event.get('headers', {})
        content_type = headers.get('content-type') or headers.get('Content-Type')
        body = event.get('body')
        if body is None:
            raise Exception("No body found in event!")
        if not content_type:
            raise Exception("No content-type header found!")

        if event.get('isBase64Encoded', False):
            body = base64.b64decode(body)
        else:
            body = body.encode() if isinstance(body, str) else body

        print(f"Decoded body length: {len(body)}")

        multipart_data = decoder.MultipartDecoder(body, content_type)
        print("Parsed multipart, parts:", len(multipart_data.parts))

        image_bytes = None
        left_line = right_line = color_click_x = color_click_y = None

        for part in multipart_data.parts:
            cd = part.headers.get(b'Content-Disposition', b'').decode()
            print("Part Content-Disposition:", cd)
            if 'name="image"' in cd:
                image_bytes = part.content
                print(f"Image bytes length: {len(image_bytes)}")
            elif 'name="left_line"' in cd:
                left_line = int(part.text)
                print(f"left_line: {left_line}")
            elif 'name="right_line"' in cd:
                right_line = int(part.text)
                print(f"right_line: {right_line}")
            elif 'name="color_click_x"' in cd:
                color_click_x = int(part.text)
                print(f"color_click_x: {color_click_x}")
            elif 'name="color_click_y"' in cd:
                color_click_y = int(part.text)
                print(f"color_click_y: {color_click_y}")

        print("Final parsed values: image_bytes:", image_bytes is not None,
              "left_line:", left_line,
              "right_line:", right_line,
              "color_click_x:", color_click_x,
              "color_click_y:", color_click_y)

        if image_bytes is None or None in (left_line, right_line, color_click_x, color_click_y):
            raise Exception("Missing one or more required fields: "
                            f"image_bytes: {image_bytes is not None}, left_line: {left_line}, "
                            f"right_line: {right_line}, color_click_x: {color_click_x}, color_click_y: {color_click_y}")

        # === NEW: check cv2.imdecode and click bounds ===
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            raise Exception("cv2.imdecode failed! The image bytes may not be a valid image or are corrupt.")
        print(f"Image shape: {image.shape}")

        if not (0 <= color_click_x < image.shape[1] and 0 <= color_click_y < image.shape[0]):
            raise Exception(f"Click coordinates ({color_click_x},{color_click_y}) out of bounds for image shape {image.shape}")

        # -------- Your original OpenCV logic below this line -----------
        pixel_bgr = image[color_click_y, color_click_x]
        pixel_hsv = cv2.cvtColor(np.uint8([[pixel_bgr]]), cv2.COLOR_BGR2HSV)[0][0]
        reference_color_hsv = pixel_hsv

        min_area = 10
        max_area = 99999

        lab_image = normalize_lab(image)
        hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        hsv_pixel = np.uint8([[[reference_color_hsv[0], reference_color_hsv[1], reference_color_hsv[2]]]])
        bgr_pixel = cv2.cvtColor(hsv_pixel, cv2.COLOR_HSV2BGR)
        lab_pixel = cv2.cvtColor(bgr_pixel, cv2.COLOR_BGR2LAB)[0][0]

        diff_lab = np.linalg.norm(lab_image - lab_pixel, axis=2)
        mask_lab = (diff_lab < LAB_TOLERANCE).astype(np.uint8) * 255

        hue_diff = np.abs(hsv_image[:, :, 0].astype(int) - int(reference_color_hsv[0]))
        hue_diff = np.minimum(hue_diff, 180 - hue_diff)
        diff_s = np.abs(hsv_image[:, :, 1].astype(int) - int(reference_color_hsv[1]))
        diff_v = np.abs(hsv_image[:, :, 2].astype(int) - int(reference_color_hsv[2]))
        mask_hsv = ((hue_diff < HSV_TOLERANCE[0]) &
                    (diff_s < HSV_TOLERANCE[1]) &
                    (diff_v < HSV_TOLERANCE[2])
                   ).astype(np.uint8) * 255

        mask = cv2.bitwise_and(mask_lab, mask_hsv)
        mask = apply_line_filter(mask, left_line, right_line)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, MORPH_KERNEL)
        mask = cv2.dilate(mask, MORPH_KERNEL, iterations=3)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        metadata = {}
        original_image_width = image.shape[1]

        annotated = image.copy()
        for i, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue
            cv2.drawContours(annotated, [contour], -1, (0, 255, 0), 2)

        _, buffer_annot = cv2.imencode('.jpg', annotated)
        annotated_base64 = base64.b64encode(buffer_annot).decode('utf-8')
        _, buffer_mask = cv2.imencode('.jpg', mask)
        mask_base64 = base64.b64encode(buffer_mask).decode('utf-8')
        _, buffer_orig = cv2.imencode('.jpg', image)
        orig_base64 = base64.b64encode(buffer_orig).decode('utf-8')

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

                crop_img = image[y_min:y_max, x_min:x_max]
                filename = f"contour_{i}_x{center[0]}_y{center[1]}.jpg"
                cv2.imwrite(os.path.join(tmpdir, filename), crop_img)

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

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "message": "Contours extracted successfully",
                "contour_metadata": metadata,
                "contour_count": len(metadata),
                "annotated_image": annotated_base64,
                "mask_image": mask_base64,
                "lambda_input_image": orig_base64,
                "reference_color_hsv": reference_color_hsv.tolist(),
                "left_line": left_line,
                "right_line": right_line
            })
        }

    except Exception as e:
        print("Exception in contour_lambda:", str(e), flush=True)
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"error": str(e)})
        }
