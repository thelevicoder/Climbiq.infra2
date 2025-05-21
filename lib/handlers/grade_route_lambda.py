import os
import json
import numpy as np
import time
import uuid
import base64
import boto3
from collections import defaultdict

# Constants and mapping
PIXEL_TO_CM = 50.0 / 272.0
DEFAULT_WALL_ANGLE = int(os.getenv('DEFAULT_WALL_ANGLE', 20))
MAX_STATIC_REACH  = 90
MAX_DYNAMIC_REACH = 105
V_GRADE_MAPPING = [
    (0, 10,  "V0"),
    (10, 16, "V1"),
    (16, 22, "V2"),
    (22, 28, "V3"),
    (28, 34, "V4"),
    (34, 40, "V5"),
    (40, 50, "V6"),
    (50, 60, "V7"),
    (60, 75, "V8"),
    (75, float('inf'), "V9+")
]

# AWS clients
ddb     = boto3.resource('dynamodb')
s3      = boto3.client('s3')
TABLE   = ddb.Table(os.environ['HISTORY_TABLE'])
BUCKET  = os.environ['IMAGE_BUCKET']


def calculate_distance(p1, p2):
    dx = (p2[0] - p1[0]) * PIXEL_TO_CM
    dy = (p2[1] - p1[1]) * PIXEL_TO_CM
    return np.hypot(dx, dy)


def calculate_angle(p1, p2):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    return abs(np.degrees(np.arctan2(dy, dx)))


def assess_move_difficulty(dist, ang, t1, t2, height_diff, wall_angle):
    if dist <= MAX_STATIC_REACH:
        d_score, dyn = (dist / MAX_STATIC_REACH) * 20, 0
    elif dist <= MAX_DYNAMIC_REACH:
        d_score, dyn = 20 + ((dist - MAX_STATIC_REACH) / (MAX_DYNAMIC_REACH - MAX_STATIC_REACH)) * 30, 5
    else:
        d_score, dyn = 50, 10
    a_score = (ang / 180) * 20
    mod = 1.0
    if t1 == t2 == 'handhold':   mod = 1.5
    if t1 == t2 == 'foothold':   mod = 1.3
    h_score = (height_diff / 200) * 20
    w_score = (wall_angle / 45) * 15
    return (d_score + a_score) * mod + h_score + w_score + dyn


def lambda_handler(event, context=None):
    print("==== grade_route_lambda invoked ====")
    print("event:", event)
    try:
        # Parse input
        payload = json.loads(event['body']) if isinstance(event.get('body'), str) else event
        print("payload:", payload)
        metadata   = payload.get('metadata', {})
        wall_angle = float(payload.get('wall_angle', DEFAULT_WALL_ANGLE))
        start_hold = payload.get('start_hold')
        end_hold   = payload.get('end_hold')
        user_email = payload.get('user_email', 'anonymous')

        # Build holds list
        holds = []
        for fname, data in metadata.items():
            if 'hold_grade' in data and 'hold_type' in data and 'center' in data:
                holds.append({
                    'center': data['center'],
                    'grade': data['hold_grade'],
                    'type':  data['hold_type']
                })

        if not holds:
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'grade': 'N/A', 'message': 'No holds to grade.'})
            }

        # Sort holds by start or by height
        if start_hold in metadata:
            base = metadata[start_hold]['center']
            holds.sort(key=lambda h: calculate_distance(base, h['center']))
        else:
            holds.sort(key=lambda h: h['center'][1])

        # Compute hold difficulty
        avg_hold  = sum(h['grade'] for h in holds) / len(holds)
        hold_diff = avg_hold * (25/10)

        # Compute movement difficulty
        move_diff = 0.0
        for i in range(len(holds)-1):
            h1, h2 = holds[i], holds[i+1]
            dist    = calculate_distance(h1['center'], h2['center'])
            ang     = calculate_angle(h1['center'], h2['center'])
            height  = max(0, (h2['center'][1] - holds[0]['center'][1]) * PIXEL_TO_CM)
            move_diff += assess_move_difficulty(dist, ang, h1['type'], h2['type'], height, wall_angle)
        if len(holds) > 1:
            move_diff = (move_diff / (len(holds)-1)) * (60/100)

        # Final grade
        total = hold_diff + move_diff
        v     = next((vg for lo, hi, vg in V_GRADE_MAPPING if lo <= total < hi), 'V0')

        # 1) Persist image to S3
        img_key   = f"history/{uuid.uuid4()}.jpg"
        full_b64  = payload.get('full_image')
        if full_b64:
            img_bytes = base64.b64decode(full_b64)
            s3.put_object(Bucket=BUCKET, Key=img_key, Body=img_bytes, ContentType='image/jpeg')
        print("avg_hold:", avg_hold)
        print("hold_diff:", hold_diff)
        print("move_diff:", move_diff)
        print("total:", total)
        print("Received metadata:", metadata)
        print("Start hold:", start_hold, "End hold:", end_hold)


        # 2) Write record to DynamoDB
        record = {
            'id':         img_key,
            'timestamp':  int(time.time()),
            'grade':      v,
            's3_key':     img_key,
            'user_email': user_email
        }
        TABLE.put_item(Item=record)

        # 3) Return result
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'total_difficulty': total, 'grade': v})
        }

    except Exception as e:
        print('Exception in grade_route_lambda:', e, flush=True)
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }
