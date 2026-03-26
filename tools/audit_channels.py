#!/usr/bin/env python3
import argparse
from pathlib import Path
from collections import defaultdict
import re

from core import TSVParserFactory

CH_RE = re.compile(r"_Ch(\d+)_M")  # extrait le numéro dans "..._Ch5_M0200..."


def read_two_header_lines(tsv_path: Path):
    with tsv_path.open("r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\n")
        line2 = f.readline().rstrip("\n")
    if not line1 or not line2:
        raise ValueError("Header incomplet (moins de 2 lignes)")
    return line1.split("\t"), line2.split("\t")


def audit_folder(folder: Path, target_field: str | None):
    hits = []
    stats = {
        "files_total": 0,
        "files_skipped": 0,
        "files_parsed": 0,
        "formats": defaultdict(int),
        "max_ch_by_master": defaultdict(int),
        "subtype_by_master": {},  # dernier subtype vu
    }

    for p in folder.rglob("*.tsv"):
        stats["files_total"] += 1

        try:
            line1, line2 = read_two_header_lines(p)
            file_format = line2[0]
            stats["formats"][file_format] += 1

            parser = TSVParserFactory.get_parser(file_format)
            mappings, device_master_sn = parser.build_channel_mappings(line1, line2)

            # infos utiles
            nb_cols_line1 = len(line1)
            nb_cols_line2 = len(line2)
            nb_channels = len(mappings)
            subtype = None
            # subtype est stocké dans chaque mapping master (même valeur), on prend le 1er
            if mappings:
                subtype = mappings[0].get("device_subtype")
                stats["subtype_by_master"][device_master_sn] = subtype

            # calcule les fields produits
            fields = []
            max_ch = 0
            for m in mappings:
                field = f"{m['channel_id']}_{m['unit']}"
                fields.append(field)

                m_ch = CH_RE.search(m["channel_id"])
                if m_ch:
                    max_ch = max(max_ch, int(m_ch.group(1)))

            stats["max_ch_by_master"][device_master_sn] = max(
                stats["max_ch_by_master"][device_master_sn], max_ch
            )

            # filtre sur un field précis si demandé
            if target_field:
                if target_field in fields:
                    hits.append(
                        {
                            "file": str(p),
                            "format": file_format,
                            "master": device_master_sn,
                            "subtype": subtype,
                            "nb_cols_line1": nb_cols_line1,
                            "nb_cols_line2": nb_cols_line2,
                            "nb_channels": nb_channels,
                            "fields": fields,
                        }
                    )

            stats["files_parsed"] += 1

        except Exception as e:
            stats["files_skipped"] += 1
            # tu peux décommenter pour voir les erreurs
            # print(f"SKIP {p}: {e}")

    return hits, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="Dossier racine contenant les TSV (ex: .../parsed)")
    ap.add_argument(
        "--target-field",
        default="M02001283_Ch5_M02001283_W",
        help="Field exact à rechercher (par défaut: M02001283_Ch5_M02001283_W). "
             "Mettre vide pour ne pas filtrer.",
    )
    args = ap.parse_args()

    folder = Path(args.folder).resolve()
    target = args.target_field.strip() if args.target_field else None

    hits, stats = audit_folder(folder, target)

    print("=== Résumé ===")
    print(f"Files total   : {stats['files_total']}")
    print(f"Files parsed  : {stats['files_parsed']}")
    print(f"Files skipped : {stats['files_skipped']}")
    print("Formats:")
    for fmt, n in sorted(stats["formats"].items(), key=lambda x: (-x[1], x[0])):
        print(f"  - {fmt}: {n}")

    print("\nMax ChN par device_master_sn (si présent):")
    for master, mx in sorted(stats["max_ch_by_master"].items(), key=lambda x: (-x[1], x[0])):
        subtype = stats["subtype_by_master"].get(master)
        print(f"  - {master}: max Ch{mx} (subtype={subtype})")

    if target:
        print(f"\n=== Fichiers qui génèrent le field '{target}' ===")
        if not hits:
            print("Aucun fichier ne génère ce field selon le mapping actuel.")
        else:
            for h in hits:
                print(f"\nFILE: {h['file']}")
                print(f"  format={h['format']} master={h['master']} subtype={h['subtype']}")
                print(f"  cols(line1)={h['nb_cols_line1']} cols(line2)={h['nb_cols_line2']} channels={h['nb_channels']}")
                # affiche seulement les fields Ch* pour être lisible
                ch_fields = [f for f in h["fields"] if "_Ch" in f]
                print("  fields (Ch*):")
                for f in ch_fields:
                    print(f"    - {f}")


if __name__ == "__main__":
    main()