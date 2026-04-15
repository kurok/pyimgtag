"""Perceptual hash duplicate detection using imagehash."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import imagehash
from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


def compute_phash(image_path: str | Path) -> str | None:
    """Compute perceptual hash for an image file.

    Args:
        image_path: Path to the image file.

    Returns:
        Hex string of the perceptual hash, or None on error.
    """
    try:
        with Image.open(image_path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute hamming distance between two hex hash strings.

    Args:
        hash1: First perceptual hash as hex string.
        hash2: Second perceptual hash as hex string.

    Returns:
        Integer hamming distance between the two hashes.
    """
    h1 = imagehash.hex_to_hash(hash1)
    h2 = imagehash.hex_to_hash(hash2)
    return int(h1 - h2)


def find_duplicate_groups(records: list[tuple[str, str]], threshold: int = 5) -> list[list[str]]:
    """Find groups of duplicate images by perceptual hash similarity.

    Uses BFS connected components to group images whose hamming distance
    is at or below the given threshold.

    Args:
        records: List of (file_path, phash_hex) tuples.
        threshold: Maximum hamming distance to consider duplicates.

    Returns:
        List of duplicate groups, where each group is a list of file paths
        with size > 1.
    """
    n = len(records)
    if n < 2:
        return []

    # Build adjacency list
    adj: dict[int, list[int]] = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            dist = hamming_distance(records[i][1], records[j][1])
            if dist <= threshold:
                adj[i].append(j)
                adj[j].append(i)

    # BFS connected components
    visited: set[int] = set()
    groups: list[list[str]] = []

    for start in range(n):
        if start in visited:
            continue
        queue: deque[int] = deque([start])
        visited.add(start)
        component: list[str] = []
        while queue:
            node = queue.popleft()
            component.append(records[node][0])
            for neighbor in adj[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        if len(component) > 1:
            groups.append(component)

    return groups
