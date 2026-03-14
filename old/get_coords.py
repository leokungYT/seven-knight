"""Click on image to get coordinates - for finding Region values"""
import cv2
import subprocess
import os

device_id = None
adb_cmd = r"adb\adb"

# Auto detect first connected device
try:
    result = subprocess.run([adb_cmd, "devices"], capture_output=True, text=True)
    lines = result.stdout.strip().split('\n')[1:]
    devices = [line.split()[0] for line in lines if 'device' in line and not line.startswith('*')]
    if devices:
        device_id = devices[0]
except:
    pass

if not device_id:
    print(f"Error: No device found via {adb_cmd} devices! Please make sure MuMu is open.")
    exit(1)

filename = "screen_coord.png"

# Capture screen
print(f"Using device: {device_id}")
print("Capturing screen...")
os.system(fr'{adb_cmd} -s {device_id} exec-out screencap -p > {filename}')

if not os.path.exists(filename):
    print("Failed to capture screen!")
    exit(1)

img = cv2.imread(filename)
if img is None:
    print(f"Error: OpenCV could not read '{filename}'. The file is corrupted or ADB capture failed.")
    exit(1)

clicks = []

def mouse_callback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        clicks.append((x, y))
        print(f"Click #{len(clicks)}: ({x}, {y})")
        
        # Draw circle
        cv2.circle(img, (x, y), 5, (0, 0, 255), -1)
        cv2.putText(img, f"({x},{y})", (x+10, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        if len(clicks) == 2:
            x1, y1 = clicks[0]
            x2, y2 = clicks[1]
            w = x2 - x1
            h = y2 - y1
            print(f"\n===== RESULT =====")
            print(f"Top-Left:     ({x1}, {y1})")
            print(f"Bottom-Right: ({x2}, {y2})")
            print(f"Region({x1}, {y1}, {w}, {h})")
            print(f"==================")
            
            # Draw rectangle
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        
        cv2.imshow("Click to get coordinates (ESC to exit)", img)

cv2.imshow("Click to get coordinates (ESC to exit)", img)
cv2.setMouseCallback("Click to get coordinates (ESC to exit)", mouse_callback)

print("Click TOP-LEFT corner first, then BOTTOM-RIGHT corner")
print("Press ESC to exit")

while True:
    key = cv2.waitKey(1)
    if key == 27:  # ESC
        break

cv2.destroyAllWindows()
