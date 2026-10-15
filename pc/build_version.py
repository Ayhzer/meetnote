"""build_version.py — Ressource de version Windows de MeetNote.exe.

Produit le texte d'un fichier de version PyInstaller (sérialisation `VSVersionInfo`),
passé à `EXE(..., version=<chemin>)`. C'est ce qu'affiche l'Explorateur dans
clic droit → Propriétés → Détails. Module sans effet de bord à l'import.
"""

PRODUCT_NAME = "MeetNote"
FILE_DESCRIPTION = "MeetNote"
ORIGINAL_FILENAME = "MeetNote.exe"
# Aucune donnée personnelle ni nom d'organisation dans les propriétés de l'exe.
COMPANY_NAME = ""

# Langue neutre (0x0000) + page de code Unicode (1200) : valeurs standard Windows.
_LANG_NEUTRE = "000004b0"
_NB_COMPOSANTES = 4


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """Convertit « 3.3.0 » en (3, 3, 0, 0) ; ValueError si le format est invalide."""
    parties = version.strip().split(".")
    valide = 1 <= len(parties) <= _NB_COMPOSANTES and all(p.isdigit() for p in parties)
    if not valide:
        raise ValueError(f"Version invalide (attendu x.y.z numérique) : {version!r}")
    nombres = [int(p) for p in parties]
    nombres += [0] * (_NB_COMPOSANTES - len(nombres))
    return tuple(nombres)  # type: ignore[return-value]


def build_version_resource(version: str) -> str:
    """Retourne le texte du fichier de version PyInstaller pour `version`."""
    nombres = version_tuple(version)
    chaine = version.strip()  # affichée telle quelle : « 3.3.0 »
    champs = [
        ("CompanyName", COMPANY_NAME),
        ("FileDescription", FILE_DESCRIPTION),
        ("FileVersion", chaine),
        ("InternalName", PRODUCT_NAME),
        ("OriginalFilename", ORIGINAL_FILENAME),
        ("ProductName", PRODUCT_NAME),
        ("ProductVersion", chaine),
    ]
    structs = ",\n          ".join(f"StringStruct('{k}', '{v}')" for k, v in champs)
    return f"""# UTF-8
#
# Généré par build_spec.py à partir de version.py — ne pas éditer.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={nombres},
    prodvers={nombres},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '{_LANG_NEUTRE}',
          [{structs}])
      ]),
    VarFileInfo([VarStruct('Translation', [0, 1200])])
  ]
)
"""
