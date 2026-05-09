"""Scratch pad — overwrite freely."""
# Verify that setting default disposition in the mux command works correctly
import subprocess

# Simulate what the fixed mux_subtitle will do
cmd = [
    'ffmpeg', '-y',
    '-i', '/home/pi/data/bt/incomplete/2013-The_Wind_Rises.mkv',
    '-i', '/tmp/translated_test.ass',
    '-map', '0', '-map', '1:0',
    '-c', 'copy',
    '-metadata:s:s:10', 'title=English (Translated)',
    '-metadata:s:s:10', 'language=eng',
    # clear default from all 10 existing subtitle streams
    '-disposition:s:0', 'none',
    '-disposition:s:1', 'none',
    '-disposition:s:2', 'none',
    '-disposition:s:3', 'none',
    '-disposition:s:4', 'none',
    '-disposition:s:5', 'none',
    '-disposition:s:6', 'none',
    '-disposition:s:7', 'none',
    '-disposition:s:8', 'none',
    '-disposition:s:9', 'none',
    # set new subtitle as default
    '-disposition:s:10', 'default',
    '-t', '600',
    '/tmp/test_mux_out.mkv',
]
result = subprocess.run(cmd, capture_output=True, text=True)
print('returncode:', result.returncode)
if result.returncode != 0:
    print(result.stderr[-500:])
else:
    # Check dispositions
    probe = subprocess.run([
        'ffprobe', '-v', 'error',
        '-select_streams', 's',
        '-show_entries', 'stream=index',
        '-show_entries', 'stream_disposition=default',
        '-show_entries', 'stream_tags=title',
        '-of', 'compact',
        '/tmp/test_mux_out.mkv'
    ], capture_output=True, text=True)
    print(probe.stdout)
