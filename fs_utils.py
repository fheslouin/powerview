import os
from pathlib import Path
from typing import List, Tuple

import logging
from shutil import move

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


def move_parsed_file(tsv_file: str) -> None:
    """
    Déplace un fichier traité dans un sous-dossier 'parsed' du device.

    Exemple :
        /srv/sftpgo/data/company/campaign/device/file.tsv
        -> /srv/sftpgo/data/company/campaign/device/parsed/file.tsv
    """
    path = Path(tsv_file)
    target_dir = path.parent / "parsed"
    target_dir.mkdir(exist_ok=True)
    new_path = target_dir / path.name
    move(str(path), str(new_path))
    logger.info("  Moved parsed file to: %s", new_path)


def move_error_file(tsv_file: str) -> None:
    """
    Déplace un fichier en erreur dans un sous-dossier 'error' du device.

    Exemple :
        /srv/sftpgo/data/company/campaign/device/file.tsv
        -> /srv/sftpgo/data/company/campaign/device/error/file.tsv
    """
    path = Path(tsv_file)
    target_dir = path.parent / "error"
    target_dir.mkdir(exist_ok=True)
    new_path = target_dir / path.name
    move(str(path), str(new_path))
    logger.info("  Moved error file to: %s", new_path)


def find_tsv_files(base_folder: str) -> List[str]:
    """
    Recherche récursivement tous les fichiers .tsv qui n'ont pas encore été traités.

    On ignore explicitement les sous-dossiers 'parsed' et 'error' pour ne pas
    retraiter les fichiers déjà déplacés.
    """
    tsv_files: List[str] = []
    for root, dirs, files in os.walk(base_folder):
        # On évite de descendre dans parsed/ et error/
        dirs[:] = [d for d in dirs if d not in ("parsed", "error")]
        for file in files:
            if file.endswith('.tsv'):
                tsv_files.append(os.path.join(root, file))
    return tsv_files
