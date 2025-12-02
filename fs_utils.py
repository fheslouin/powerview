import os
from pathlib import Path
from typing import List, Tuple

import logging

logger = logging.getLogger("tsv_parser")


def extract_path_components(tsv_path: str, base_folder: str) -> Tuple[str, str, str]:
    """
    Extrait le bucket, la campagne et le numéro de série du device
    à partir du chemin du fichier TSV.

    Structure attendue :
        base_folder/my_client/campaign/device_master_sn/file.tsv

    Retourne :
        (bucket_name, campaign_name, device_master_sn)
    """
    path = Path(tsv_path)
    relative_path = path.relative_to(base_folder)
    parts = relative_path.parts

    if len(parts) < 4:
        raise ValueError(f"Invalid path structure: {tsv_path}")

    bucket_name = parts[0]       # my_client (top folder)
    campaign_name = parts[1]     # campaign folder
    device_master_sn = parts[2]  # device serial number folder

    return bucket_name, campaign_name, device_master_sn


def rename_parsed_file(tsv_file: str) -> None:
    """
    Renomme un fichier traité en préfixant son nom par PARSED_.
    """
    path = Path(tsv_file)
    new_name = f"PARSED_{path.name}"
    new_path = path.parent / new_name
    path.rename(new_path)
    logger.info("  Renamed to: %s", new_name)


def find_tsv_files(base_folder: str) -> List[str]:
    """
    Recherche récursivement tous les fichiers .tsv qui n'ont pas encore été parsés
    (c'est-à-dire qui ne commencent pas par PARSED_).
    """
    tsv_files: List[str] = []
    for root, dirs, files in os.walk(base_folder):
        for file in files:
            if file.endswith('.tsv') and not file.startswith('PARSED_'):
                tsv_files.append(os.path.join(root, file))
    return tsv_files
