#!/usr/bin/env python3
"""
Advanced Generic Video Downloader with Proper Progress Bar
Enhanced version with file size display and better progress tracking
"""

import argparse
import csv
import os
import logging
import subprocess
import sys
import shutil
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
import json
import threading
import time

def check_yt_dlp_installed():
    """Check if yt-dlp is available via Python module."""
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], 
                      capture_output=True, check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False

def setup_logger(output_dir):
    """Sets up a logger to file and console."""
    log_file = output_dir / "download.log"
    logger = logging.getLogger('video_downloader')
    logger.setLevel(logging.INFO)

    # File handler for logging
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)

    # Formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger

def read_urls_from_args(args):
    return args.urls

def read_urls_from_file(file_path):
    urls = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
    except FileNotFoundError:
        print(f"Error: File {file_path} not found.")
    return urls

def read_urls_from_csv(file_path):
    urls = []
    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if row:
                    url = row[0].strip()
                    if url:
                        urls.append(url)
    except FileNotFoundError:
        print(f"Error: CSV file {file_path} not found.")
    return urls

class DownloadProgress:
    """Class to track download progress for a single URL"""
    def __init__(self, url, index, total):
        self.url = url
        self.index = index
        self.total = total
        self.percentage = 0
        self.speed = "0B/s"
        self.eta = "00:00"
        self.size = "Unknown"
        self.downloaded = "0B"
        self.filename = "Unknown"
        self.completed = False
        self.error = None
        
    def update(self, percentage, speed, eta, size=None, downloaded=None, filename=None):
        self.percentage = percentage
        self.speed = speed
        self.eta = eta
        if size:
            self.size = size
        if downloaded:
            self.downloaded = downloaded
        if filename:
            self.filename = filename
            
    def set_completed(self):
        self.completed = True
        self.percentage = 100
        self.downloaded = self.size  # When completed, downloaded equals total size
        
    def set_error(self, error):
        self.error = error

def download_video(url, output_template, quality, extract_audio, logger, progress_obj):
    """Download a single video using yt-dlp with progress tracking."""
    command = [
        sys.executable,
        "-m", "yt_dlp",
        '--newline',
        '-o', output_template,
        '--no-warnings',
    ]

    if quality != 'best':
        command.extend(['-S', f'res:{quality},ext:mp4:m4a'])

    if extract_audio:
        command.extend(['-x', '--audio-format', 'mp3'])

    command.extend([
        '--add-metadata',
        '--no-overwrites',
        '--continue',
        '--restrict-filenames',
        url
    ])

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                 universal_newlines=True, bufsize=1, shell=True)
        
        # Patterns to match progress information
        percentage_pattern = re.compile(r'\[download\]\s+([\d.]+)%')
        speed_pattern = re.compile(r'at\s+([\d.]+\s*[KMGT]?iB/s)')
        eta_pattern = re.compile(r'ETA\s+([\d:]+)')
        size_pattern = re.compile(r'of\s+([\d.]+\s*[KMGT]?iB)')
        downloaded_pattern = re.compile(r'([\d.]+\s*[KMGT]?iB)\s+at')
        filename_pattern = re.compile(r'Destination:\s+(.+)')
        complete_pattern = re.compile(r'\[download\]\s+100%')
        
        for line in process.stdout:
            line = line.strip()
            logger.info(line)
            
            # Check for filename
            filename_match = filename_pattern.search(line)
            if filename_match:
                progress_obj.filename = filename_match.group(1)
            
            # Check for percentage
            percentage_match = percentage_pattern.search(line)
            if percentage_match:
                progress_obj.percentage = float(percentage_match.group(1))
                
                # Try to find size, speed, ETA, and downloaded amount in the same line
                size_match = size_pattern.search(line)
                if size_match:
                    progress_obj.size = size_match.group(1)
                    
                speed_match = speed_pattern.search(line)
                if speed_match:
                    progress_obj.speed = speed_match.group(1)
                    
                eta_match = eta_pattern.search(line)
                if eta_match:
                    progress_obj.eta = eta_match.group(1)
                    
                downloaded_match = downloaded_pattern.search(line)
                if downloaded_match:
                    progress_obj.downloaded = downloaded_match.group(1)
            
            # Check for completion
            if complete_pattern.search(line):
                progress_obj.set_completed()
        
        process.wait()
        if process.returncode == 0:
            if not progress_obj.completed:
                progress_obj.set_completed()
            logger.info(f"SUCCESS: {url}")
            return (url, True, "Download completed")
        else:
            error_msg = f"FAILED: {url} - yt-dlp returned code {process.returncode}"
            progress_obj.set_error(error_msg)
            logger.error(error_msg)
            return (url, False, error_msg)

    except Exception as e:
        error_msg = f"Exception for {url}: {str(e)}"
        progress_obj.set_error(error_msg)
        logger.error(error_msg)
        return (url, False, error_msg)

def display_progress(progress_trackers, stop_event):
    """Display progress for all downloads in a separate thread"""
    while not stop_event.is_set() or any(not tracker.completed and not tracker.error for tracker in progress_trackers):
        # Clear screen (works on both Windows and Unix)
        os.system('cls' if os.name == 'nt' else 'clear')
        
        print("╔══════════════════════════════════════════════════════════════════════╗")
        print("║                       VIDEO DOWNLOAD PROGRESS                        ║")
        print("╠══════════════════════════════════════════════════════════════════════╣")
        
        for tracker in progress_trackers:
            if tracker.completed:
                status = "✓ COMPLETED"
                color_code = "\033[92m"  # Green
            elif tracker.error:
                status = "✗ ERROR"
                color_code = "\033[91m"  # Red
            else:
                status = "↓ DOWNLOADING"
                color_code = "\033[93m"  # Yellow
                
            # Shorten filename for display
            short_filename = tracker.filename
            if len(short_filename) > 30:
                short_filename = "..." + short_filename[-27:]
                
            print(f"║ {color_code}{status:<12}\033[0m {tracker.index:2d}/{tracker.total:2d} {short_filename:<30} ║")
            print(f"║    Progress: {tracker.percentage:6.1f}% | {tracker.downloaded}/{tracker.size} | Speed: {tracker.speed:>10} ║")
            print(f"║    ETA: {tracker.eta:>8}                                                  ║")
            print("╠──────────────────────────────────────────────────────────────────────╣")
        
        print("║ Press Ctrl+C to stop                                                       ║")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        
        # Refresh every second
        stop_event.wait(1)
    
    # Final update
    os.system('cls' if os.name == 'nt' else 'clear')
    print_progress_final(progress_trackers)

def print_progress_final(progress_trackers):
    """Print final progress summary"""
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║                           DOWNLOAD SUMMARY                           ║")
    print("╠══════════════════════════════════════════════════════════════════════╣")
    
    successful = sum(1 for t in progress_trackers if t.completed)
    failed = sum(1 for t in progress_trackers if t.error)
    
    print(f"║ Completed: {successful:2d} | Failed: {failed:2d} | Total: {len(progress_trackers):2d}                            ║")
    print("╠══════════════════════════════════════════════════════════════════════╣")
    
    for tracker in progress_trackers:
        status = "✓ COMPLETED" if tracker.completed else "✗ ERROR" if tracker.error else "? UNKNOWN"
        color_code = "\033[92m" if tracker.completed else "\033[91m" if tracker.error else "\033[93m"
        
        # Shorten filename for display
        short_filename = tracker.filename
        if len(short_filename) > 40:
            short_filename = "..." + short_filename[-37:]
            
        print(f"║ {color_code}{status:<12}\033[0m {tracker.index:2d}/{tracker.total:2d} {short_filename:<40} ║")
        if tracker.completed:
            print(f"║    Size: {tracker.size:<54} ║")
        print("╠──────────────────────────────────────────────────────────────────────╣")
    
    print("╚══════════════════════════════════════════════════════════════════════╝")

def main():
    parser = argparse.ArgumentParser(description='Advanced Video Downloader with Progress Bar', 
                                    formatter_class=argparse.RawDescriptionHelpFormatter,
                                    epilog="""
Examples:
  %(prog)s --urls "https://example.com/video1" "https://example.com/video2"
  %(prog)s --file links.txt --quality 720p --threads 3
  %(prog)s --csv links.csv --extract-audio --output-dir ./my_downloads
                                    """)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--urls', nargs='+', help='List of video URLs')
    group.add_argument('--file', type=str, help='Text file with URLs (one per line)')
    group.add_argument('--csv', type=str, help='CSV file with URLs (URLs in first column)')

    parser.add_argument('--output-dir', '-o', type=str, default='./downloads', help='Output directory (default: ./downloads)')
    parser.add_argument('--quality', '-q', type=str, default='best', choices=['best', '720p', '480p', '360p'], help='Video quality (default: best)')
    parser.add_argument('--extract-audio', '-x', action='store_true', help='Extract audio only (MP3)')
    parser.add_argument('--threads', '-t', type=int, default=1, help='Simultaneous downloads (default: 1)')
    parser.add_argument('--retries', '-r', type=int, default=2, help='Retry attempts for failed downloads (default: 2)')

    args = parser.parse_args()

    if not check_yt_dlp_installed():
        print("ERROR: yt-dlp Python module is not installed.")
        print("Please install it with: pip install yt-dlp")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(output_dir)
    logger.info("Script started with progress tracking")

    # Get URLs
    if args.urls:
        urls = read_urls_from_args(args)
    elif args.file:
        urls = read_urls_from_file(args.file)
    elif args.csv:
        urls = read_urls_from_csv(args.csv)
    else:
        parser.print_help()
        sys.exit(1)

    if not urls:
        logger.error("No URLs found")
        sys.exit(1)

    logger.info(f"Found {len(urls)} URLs")

    output_template = str(output_dir / '%(title)s.%(ext)s')
    results = []
    
    # Create progress trackers for each URL
    progress_trackers = [DownloadProgress(url, i+1, len(urls)) for i, url in enumerate(urls)]
    
    # Create and start progress display thread
    stop_event = threading.Event()
    progress_thread = threading.Thread(target=display_progress, args=(progress_trackers, stop_event))
    progress_thread.daemon = True
    progress_thread.start()

    try:
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            # Submit all download tasks
            future_to_url = {}
            for i, url in enumerate(urls):
                future = executor.submit(
                    download_video,
                    url,
                    output_template,
                    args.quality,
                    args.extract_audio,
                    logger,
                    progress_trackers[i]
                )
                future_to_url[future] = url

            # Process results as they complete
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    error_msg = f'{url} generated exception: {exc}'
                    logger.error(error_msg)
                    # Find the progress tracker for this URL and mark it as error
                    for tracker in progress_trackers:
                        if tracker.url == url:
                            tracker.set_error(error_msg)
                    results.append((url, False, error_msg))
        
        # Stop the progress display thread
        stop_event.set()
        progress_thread.join(timeout=1)
        
    except KeyboardInterrupt:
        print("\nDownload interrupted by user")
        stop_event.set()
        sys.exit(1)
    
    # Final summary
    successful = sum(1 for r in results if r[1])
    failed = len(results) - successful
    
    print(f"\nDownload completed. Successful: {successful}, Failed: {failed}")
    print(f"Check '{output_dir / 'download.log'}' for details.")

if __name__ == "__main__":
    main()