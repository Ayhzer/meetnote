"""build_paths.py — Chemins et listes de fichiers utilisés par les scripts de build.

Module volontairement sans effet de bord à l'import : build_spec.py exécute le
build au niveau module, il ne peut donc pas être importé par un test.
"""

import math
import os
import shutil
import subprocess
import sys
import zipfile

# Variable d'environnement permettant de désigner une autre installation Python.
ENV_PYTHON_HOME = "MEETNOTE_PYTHON_HOME"
# Variable d'environnement désignant le ffmpeg.exe à embarquer (build « essentials »).
ENV_FFMPEG = "MEETNOTE_FFMPEG"
# Variable d'environnement désignant l'ISCC.exe d'Inno Setup 6 (installeur).
ENV_ISCC = "MEETNOTE_ISCC"

OCTETS_PAR_MO = 1024 * 1024  # même unité que modeles.py
# Un ffmpeg « essentials » pèse ~80-100 Mo ; la build « full » dépasse 200 Mo.
POIDS_MAX_FFMPEG_MO = 120
# Au-delà, l'archive devient pénible à télécharger pour un utilisateur.
POIDS_MAX_ARCHIVE_MO = 300
# Une compilation de l'installeur (~1 Go de fichiers, compression lzma2) est longue.
DELAI_MAX_ISCC_S = 1800
DELAI_MAX_SIGNATURE_S = 30
# Script Inno Setup, versionné à côté du build.
SCRIPT_ISS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "installer", "meetnote.iss")
# Certificat auto-signé MeetNote (CurrentUser\My), créé via setup_codesign.ps1.
SERVEUR_HORODATAGE = "http://timestamp.digicert.com"  # TSA publique gratuite
# Nombre de caractères de la sortie d'ISCC ou de PowerShell repris dans une alerte.
LONGUEUR_MAX_DIAGNOSTIC = 300
# Nombre de restes nommés dans l'avertissement (le reste est résumé par « … »).
MAX_RESTES_NOMMES = 8


def resolve_python_home() -> str:
    """Retourne la racine de l'installation Python qui porte les dépendances.

    Priorité à la variable d'environnement MEETNOTE_PYTHON_HOME (si définie et
    non vide), sinon `sys.base_prefix` : le build se lance avec
    `py -3.12 build_spec.py`, qui désigne l'installation Python 3.12 en cours.
    """
    override = os.environ.get(ENV_PYTHON_HOME, "").strip()
    return override or sys.base_prefix


def resolve_site_packages(python_home: str | None = None) -> str:
    """Retourne le dossier site-packages de la racine Python donnée (ou résolue)."""
    home = python_home if python_home is not None else resolve_python_home()
    return os.path.join(home, "Lib", "site-packages")


def dist_dir_name(version: str, slim: bool = False) -> str:
    """Nom du dossier livré : `MeetNote-<version>` (ou `MeetNote-slim-<version>`)."""
    base = "MeetNote-slim" if slim else "MeetNote"
    return f"{base}-{version.strip()}"


def nom_archive(version: str, slim: bool = False) -> str:
    """Nom de l'archive à télécharger : `MeetNote-<version>.zip` (slim cohérent)."""
    return dist_dir_name(version, slim) + ".zip"


# Paquets Python copiés tels quels dans le bundle : (dossier dans site-packages,
# destination dans le bundle). Aucun modèle Whisper ici : il est téléchargé au
# premier lancement (modeles.py) et vit hors du paquet.
PAQUETS_EMBARQUES = (
    (os.path.join("faster_whisper", "assets"), "faster_whisper/assets"),
    ("_sounddevice_data", "_sounddevice_data"),
    ("ctranslate2", "ctranslate2"),
    ("tokenizers", "tokenizers"),
    ("soundcard", "soundcard"),
)

# Exécutable copié à la racine du bundle : MEETNOTE_FFMPEG, à défaut le PATH.
BINAIRES_EMBARQUES = ("ffmpeg.exe",)


def _vers_spec(chemin: str) -> str:
    """Chemin à barres obliques, lisible dans une chaîne brute du spec."""
    return chemin.replace("\\", "/")


def resoudre_ffmpeg(environ, trouver=shutil.which, journal=print,
                    existe=os.path.isfile) -> str | None:
    """Chemin du ffmpeg à embarquer, ou None s'il est introuvable.

    MEETNOTE_FFMPEG prime s'il désigne un fichier existant. Défini mais invalide,
    il est signalé en clair et le build se replie sur le PATH (un build reste
    possible, l'avertissement évite l'erreur silencieuse). Sinon : le PATH.
    """
    voulu = environ.get(ENV_FFMPEG, "").strip()
    if voulu:
        if existe(voulu):
            return voulu
        journal(f"{ENV_FFMPEG}={voulu} ne désigne aucun fichier — "
                "repli sur le PATH")
    nom = BINAIRES_EMBARQUES[0]
    return trouver(nom.replace(".exe", "")) or trouver(nom)


def octets_en_mo(octets: int) -> float:
    """Convertit des octets en Mo (1 Mo = 1 048 576 octets)."""
    return octets / OCTETS_PAR_MO


def mo_arrondi_au_dessus(octets: int) -> float:
    """Mo arrondis à UNE décimale, toujours par excès.

    Un arrondi au plus proche affichait « 300 Mo, au-delà de 300 Mo » pour
    300 Mo + 1 octet ; par excès, un poids supérieur à un seuil entier n'est
    jamais affiché égal à lui.
    """
    return math.ceil(octets_en_mo(octets) * 10) / 10


def avertissement_poids_ffmpeg(octets: int) -> str | None:
    """Message si le ffmpeg retenu dépasse POIDS_MAX_FFMPEG_MO, sinon None."""
    if octets_en_mo(octets) <= POIDS_MAX_FFMPEG_MO:
        return None
    return (f"ffmpeg complet détecté ({mo_arrondi_au_dessus(octets):.1f} Mo, "
            f"au-delà de {POIDS_MAX_FFMPEG_MO} Mo) : utilisez la build "
            f"essentials via {ENV_FFMPEG}")


def avertissement_poids_archive(octets: int) -> str | None:
    """Message si l'archive dépasse POIDS_MAX_ARCHIVE_MO, sinon None."""
    if octets_en_mo(octets) <= POIDS_MAX_ARCHIVE_MO:
        return None
    return (f"archive de {mo_arrondi_au_dessus(octets):.1f} Mo, au-delà de "
            f"{POIDS_MAX_ARCHIVE_MO} Mo : vérifiez le ffmpeg embarqué et les "
            "restes d'anciens builds")


def _signaler(message: str, journal, alertes: list[str] | None) -> None:
    """Journalise une alerte et la retient pour le bilan final."""
    journal(f"ATTENTION : {message}")
    if alertes is not None:
        alertes.append(message)


def composer_bilan(alertes: list[str]) -> list[str]:
    """Lignes du bilan final : les alertes, répétées après le journal PyInstaller."""
    if not alertes:
        return []
    return [f"BILAN — {len(alertes)} alerte(s) à lire avant de livrer :",
            *(f"  - {alerte}" for alerte in alertes)]


def _preparer_ffmpeg(trouve: str, etape: str | None, copier, journal,
                     alertes: list[str] | None) -> str:
    """Chemin à embarquer : une copie nommée ffmpeg.exe si le fichier a un autre nom.

    PyInstaller garde le nom du fichier source, or l'application ne cherche que
    `ffmpeg.exe` (notion_push._find_ffmpeg). Sans dossier d'étape, le décalage
    est seulement signalé.
    """
    nom = BINAIRES_EMBARQUES[0]
    if os.path.basename(trouve).lower() == nom:
        return trouve
    if not etape:
        _signaler(f"{trouve} serait embarqué sous son propre nom, que "
                  f"l'application ne cherche pas (elle attend {nom})",
                  journal, alertes)
        return trouve
    os.makedirs(etape, exist_ok=True)
    copie = os.path.join(etape, nom)
    copier(trouve, copie)
    journal(f"{trouve} embarqué sous le nom {nom} (copie : {copie})")
    return copie


def collecter_datas(site_packages: str, trouver_binaire=shutil.which,
                    journal=print, environ=None,
                    taille=os.path.getsize, alertes: list[str] | None = None,
                    etape: str | None = None,
                    copier=shutil.copy2) -> list[tuple[str, str]]:
    """Liste (source, destination) des données à embarquer dans le bundle.

    Les paquets absents de `site_packages` sont ignorés. ffmpeg est résolu par
    `resoudre_ffmpeg` (MEETNOTE_FFMPEG puis PATH) ; absent ou trop lourd, il est
    signalé par `journal` et ajouté à `alertes` (pour le bilan final). Un ffmpeg
    qui ne s'appelle pas ffmpeg.exe est copié sous ce nom dans `etape`. Aucun
    modèle Whisper n'est ajouté.
    """
    environ = os.environ if environ is None else environ
    datas = []
    for sous_dossier, destination in PAQUETS_EMBARQUES:
        source = os.path.join(site_packages, sous_dossier)
        if os.path.isdir(source):
            datas.append((_vers_spec(source), destination))

    def noter(message):
        journal(message)
        if alertes is not None:
            alertes.append(message)

    nom = BINAIRES_EMBARQUES[0]
    trouve = resoudre_ffmpeg(environ, trouver_binaire, noter)
    if not trouve:
        noter(f"{nom} not found ({ENV_FFMPEG} ou PATH) — "
              "audio conversion requires ffmpeg at runtime")
        return datas
    source = _preparer_ffmpeg(trouve, etape, copier, journal, alertes)
    datas.append((_vers_spec(source), "."))
    try:
        octets = taille(trouve)
    except OSError:
        journal(f"Bundling {nom}: {trouve} (poids illisible)")
        return datas
    journal(f"Bundling {nom}: {trouve} ({octets_en_mo(octets):.0f} Mo)")
    alerte = avertissement_poids_ffmpeg(octets)
    if alerte:
        _signaler(alerte, journal, alertes)
    return datas


def formater_datas(datas: list[tuple[str, str]]) -> str:
    """Bloc de lignes `(r'src', r'dst'),` à insérer dans `datas=[...]` du spec."""
    return "\n".join(f"    (r'{src}', r'{dst}')," for src, dst in datas)


def lister_fichiers(dossier: str) -> list[str]:
    """Chemins relatifs (barres obliques) de tous les fichiers d'un dossier."""
    return [os.path.relpath(os.path.join(racine, f), dossier).replace("\\", "/")
            for racine, _dossiers, fichiers in os.walk(dossier) for f in fichiers]


def _dossiers_parents(chemin: str) -> list[str]:
    """Dossiers ancêtres d'un chemin relatif, du plus haut au plus bas."""
    morceaux = chemin.split("/")[:-1]
    return ["/".join(morceaux[:i + 1]) for i in range(len(morceaux))]


def lister_restes(livres: list[str], frais: list[str]) -> list[str]:
    """Restes d'anciens builds : ce que le dossier livré a en plus de la sortie fraîche.

    Fonction pure sur des chemins relatifs. Un dossier entièrement absent de la
    sortie fraîche est nommé une fois (`_internal/torch`), pas fichier par fichier.
    """
    presents = {chemin.casefold() for chemin in frais}
    dossiers_frais = {d.casefold() for chemin in frais
                      for d in _dossiers_parents(chemin)}
    restes = set()
    for chemin in livres:
        if chemin.casefold() in presents:
            continue
        absents = [d for d in _dossiers_parents(chemin)
                   if d.casefold() not in dossiers_frais]
        restes.add(absents[0] if absents else chemin)
    return sorted(restes)


def creer_archive(dossier_source: str, chemin_zip: str, nom_racine: str) -> str:
    """Zippe `dossier_source` sous la racine `nom_racine` de l'archive.

    Une archive existante du même nom est remplacée : c'est un artefact de build.
    """
    if os.path.exists(chemin_zip):
        os.remove(chemin_zip)
    with zipfile.ZipFile(chemin_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for relatif in lister_fichiers(dossier_source):
            archive.write(os.path.join(dossier_source, relatif),
                          f"{nom_racine}/{relatif}")
    return chemin_zip


def signaler_restes(dossier_source: str, dossier_livre: str, journal=print,
                    alertes: list[str] | None = None) -> list[str]:
    """Nomme, sans les supprimer, les restes d'anciens builds du dossier livré."""
    if not os.path.isdir(dossier_livre):
        return []
    restes = lister_restes(lister_fichiers(dossier_livre),
                           lister_fichiers(dossier_source))
    if restes:
        apercu = ", ".join(restes[:MAX_RESTES_NOMMES])
        suite = " …" if len(restes) > MAX_RESTES_NOMMES else ""
        _signaler(f"{len(restes)} reste(s) d'anciens builds dans {dossier_livre}, "
                  f"absents de l'archive et non supprimés : {apercu}{suite}",
                  journal, alertes)
    return restes


def produire_archive(dossier_source: str, version: str, dossier_sortie: str,
                     slim: bool = False, journal=print,
                     alertes: list[str] | None = None,
                     dossier_livre: str | None = None) -> str:
    """Produit `MeetNote-<version>.zip` dans `dossier_sortie`.

    L'archive ne contient que `dossier_source`, la sortie fraîche de PyInstaller :
    rien d'un ancien build. `dossier_livre`, s'il est donné, est comparé à cette
    sortie pour nommer ses restes (jamais supprimés). Le poids de l'archive est
    affiché, avec un avertissement au-delà de POIDS_MAX_ARCHIVE_MO.
    """
    if dossier_livre:
        signaler_restes(dossier_source, dossier_livre, journal, alertes)
    chemin_zip = os.path.join(dossier_sortie, nom_archive(version, slim))
    journal(f"Création de {chemin_zip} ...")
    creer_archive(dossier_source, chemin_zip, dist_dir_name(version, slim))
    octets = os.path.getsize(chemin_zip)
    journal(f"Archive prête : {chemin_zip} ({octets_en_mo(octets):.1f} Mo)")
    alerte = avertissement_poids_archive(octets)
    if alerte:
        _signaler(alerte, journal, alertes)
    return chemin_zip


def nom_installeur(version: str, slim: bool = False) -> str:
    """Nom de l'installeur : `MeetNote-<version>-installation.exe` (slim cohérent)."""
    return dist_dir_name(version, slim) + "-installation.exe"


def emplacements_iscc(environ) -> list[str]:
    """Emplacements habituels d'Inno Setup 6 : par utilisateur, puis par machine.

    Les chemins viennent des variables d'environnement du poste ; une variable
    absente ne produit aucun emplacement (jamais de lecteur écrit en dur).
    """
    local = environ.get("LOCALAPPDATA", "").strip()
    racines = (
        os.path.join(local, "Programs") if local else "",
        environ.get("ProgramFiles(x86)", "").strip(),
        environ.get("ProgramFiles", "").strip(),
    )
    return [os.path.join(racine, "Inno Setup 6", "ISCC.exe")
            for racine in racines if racine]


def trouver_iscc(environ, existe=os.path.isfile, journal=print) -> str | None:
    """Chemin d'ISCC.exe, ou None si Inno Setup 6 est introuvable.

    MEETNOTE_ISCC prime s'il désigne un fichier existant. Défini mais invalide,
    il est signalé en clair et la recherche se replie sur les emplacements
    habituels (même règle que MEETNOTE_FFMPEG).
    """
    voulu = environ.get(ENV_ISCC, "").strip()
    if voulu:
        if existe(voulu):
            return voulu
        journal(f"{ENV_ISCC}={voulu} ne désigne aucun fichier — "
                "repli sur les emplacements habituels d'Inno Setup 6")
    for chemin in emplacements_iscc(environ):
        if existe(chemin):
            return chemin
    return None


def commande_iscc(iscc: str, iss: str, version: str, dossier_source: str,
                  dossier_sortie: str, slim: bool = False) -> list[str]:
    """Ligne de commande ISCC (liste d'arguments, sans shell).

    `/D` passe les deux macros qu'attend meetnote.iss, `/O` le dossier de sortie
    (collé, sans espace) et `/F` le nom de l'installeur sans extension, pour que
    la variante slim n'écrase pas celui du paquet complet.
    """
    base = nom_installeur(version, slim).removesuffix(".exe")
    return [iscc,
            f"/DAppVersion={version.strip()}",
            f"/DDossierSource={dossier_source}",
            f"/O{dossier_sortie}",
            f"/F{base}",
            iss]


def commande_signature(chemin: str) -> list[str]:
    """Commande PowerShell qui signe `chemin` avec le certificat MeetNote."""
    cible = chemin.replace("'", "''")  # apostrophe doublée : littéral PowerShell
    script = rf"""
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object {{ $_.Subject -like '*MeetNote*' -and $_.HasPrivateKey }} |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1
if (-not $cert) {{
    Write-Error 'Certificat MeetNote introuvable dans CurrentUser\My'
    exit 1
}}
$r = Set-AuthenticodeSignature -FilePath '{cible}' -Certificate $cert `
        -HashAlgorithm SHA256 -TimestampServer '{SERVEUR_HORODATAGE}'
if ($r.Status -ne 'Valid') {{
    Write-Error ("Signature echouee: " + $r.StatusMessage)
    exit 1
}}
Write-Host ("Signe: " + $r.Status + " — " + $cert.Thumbprint)
"""
    return ["powershell", "-NonInteractive", "-Command", script]


def signer_fichier(chemin: str, executer=subprocess.run, journal=print) -> bool:
    """Signe `chemin` si le certificat est présent ; sinon le dit, sans échouer.

    Retourne True si le fichier est signé. Le build n'échoue jamais pour une
    signature : un exe non signé reste utilisable.
    """
    # Commande construite ici, sans entrée externe : S603/S607 non pertinents.
    try:
        r = executer(commande_signature(chemin), capture_output=True, text=True,
                     timeout=DELAI_MAX_SIGNATURE_S)
    except (OSError, subprocess.TimeoutExpired) as erreur:
        journal(f"WARNING: signature de {chemin} impossible — {erreur}")
        return False
    if r.returncode != 0:
        journal(f"WARNING: signature de {chemin} échouée — "
                f"{r.stderr.strip()[:LONGUEUR_MAX_DIAGNOSTIC]}")
        return False
    journal(f"{os.path.basename(chemin)} : {r.stdout.strip()}")
    return True


def _diagnostic(resultat) -> str:
    """Fin de la sortie d'ISCC, là où il écrit son erreur."""
    texte = (resultat.stderr or resultat.stdout or "").strip()
    return texte[-LONGUEUR_MAX_DIAGNOSTIC:]


def produire_installeur(dossier_source: str, version: str, dossier_sortie: str,
                        slim: bool = False, journal=print,
                        alertes: list[str] | None = None, environ=None,
                        iss: str = SCRIPT_ISS, executer=subprocess.run,
                        existe=os.path.isfile, signer=signer_fichier) -> str | None:
    """Compile l'installeur Inno Setup et le signe ; None s'il n'a pas été produit.

    Sans Inno Setup, avec un script absent ou une compilation en échec, le build
    ne tombe pas : l'alerte part au bilan et le zip portable reste le livrable.
    """
    environ = os.environ if environ is None else environ
    if not existe(iss):
        _signaler(f"installeur non produit : script {iss} introuvable",
                  journal, alertes)
        return None
    iscc = trouver_iscc(environ, existe,
                        lambda message: _signaler(message, journal, alertes))
    if not iscc:
        _signaler(f"Inno Setup 6 introuvable ({ENV_ISCC} ou emplacements "
                  "habituels) : installeur non produit, le zip portable reste "
                  "le livrable", journal, alertes)
        return None
    commande = commande_iscc(iscc, iss, version, dossier_source, dossier_sortie,
                             slim)
    journal(f"Compilation de l'installeur avec {iscc} ...")
    try:
        resultat = executer(commande, capture_output=True, text=True,
                            timeout=DELAI_MAX_ISCC_S)
    except (OSError, subprocess.TimeoutExpired) as erreur:
        _signaler(f"installeur non produit : ISCC inutilisable ({erreur}), "
                  "le zip portable reste le livrable", journal, alertes)
        return None
    chemin = os.path.join(dossier_sortie, nom_installeur(version, slim))
    if resultat.returncode != 0 or not existe(chemin):
        _signaler(f"installeur non produit : ISCC a échoué (code "
                  f"{resultat.returncode}) — {_diagnostic(resultat)} — le zip "
                  "portable reste le livrable", journal, alertes)
        return None
    signer(chemin, journal=journal)
    journal(f"Installeur prêt : {chemin}")
    return chemin
