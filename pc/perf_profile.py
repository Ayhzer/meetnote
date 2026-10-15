"""
perf_profile.py — Calibre la transcription Whisper sur les capacites du poste.

Mesure du 2026-08-29 sur Core Ultra 7 268V (8 coeurs, 18 min d'audio, modele medium) :

    1 fil x 6 threads   899 s   (comportement historique)
    2 fils x 4 threads  669 s   soit 1,35x
    3 fils x 2 threads  641 s   soit 1,40x, mais +27 % de RAM pour +4 %

Enseignement : le decodage Whisper est autoregressif, il ne se parallelise quasiment
pas au sein d'un segment — monter le nombre de threads d'un seul flux ne rapporte
rien (4, 6 et 8 threads tiennent en 12 %). Le gain vient de traiter *plusieurs
segments a la fois*. D'ou un profil exprime en (fils, threads par fil).
"""
import os

# Quantite de RAM a reserver par fil de transcription, en Gio. Releve : 1,1 Gio de
# RSS a un fil, 1,5 a deux, 1,9 a trois sur le modele medium ; large-v3 monte a 2,6.
RAM_PAR_FIL_GIO = 1.0

# Charge CPU (en %) au-dela de laquelle on considere le poste occupe par autre chose.
# Volontairement haut : un poste bureautique ordinaire (navigateur, Outlook, Teams au
# repos) oscille entre 15 et 30 %. A 35 %, l'automatique retombait a un seul flux la
# plupart du temps et le gain du parallelisme n'etait jamais visible.
SEUIL_POSTE_OCCUPE = 60.0

# Profils disponibles cote reglages utilisateur.
PROFILS = ("auto", "rapide", "econome")

# Libelles pour l'interface, dans l'ordre d'affichage.
LIBELLES = {
    "auto":    "Auto — s'adapte a la charge du poste",
    "rapide":  "Rapide — exploite le poste au maximum",
    "econome": "Econome — laisse le poste disponible",
}


class ProfilExecution:
    """Parametres d'execution retenus pour une transcription."""

    def __init__(self, fils: int, threads: int, motif: str):
        self.fils = fils
        self.threads = threads
        self.motif = motif

    def __eq__(self, autre) -> bool:
        return (isinstance(autre, ProfilExecution)
                and self.fils == autre.fils
                and self.threads == autre.threads)

    def __repr__(self) -> str:
        return (f"ProfilExecution(fils={self.fils}, threads={self.threads}, "
                f"motif={self.motif!r})")

    @property
    def resume(self) -> str:
        """Ligne courte affichable dans la barre de statut ou les reglages."""
        fil_s = "1 flux" if self.fils == 1 else f"{self.fils} flux"
        return f"{fil_s} x {self.threads} threads — {self.motif}"


def coeurs_physiques() -> int:
    """Nombre de coeurs physiques, avec repli sur les coeurs logiques puis sur 4."""
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except (ImportError, OSError):
        pass
    return os.cpu_count() or 4


def ram_libre_gio() -> float:
    """RAM disponible en Gio. Renvoie une valeur prudente si la mesure echoue."""
    try:
        import psutil
        return psutil.virtual_memory().available / 2 ** 30
    except (ImportError, OSError):
        return 2.0


def charge_cpu() -> float:
    """Charge CPU instantanee en %, mesuree sur un court intervalle."""
    try:
        import psutil
        return float(psutil.cpu_percent(interval=0.3))
    except (ImportError, OSError):
        return 0.0


def _fils_max_possibles(coeurs: int, ram_gio: float) -> int:
    """Plafond de fils imposé par le materiel, independamment du profil choisi.

    Trois fils demandent au moins neuf coeurs pour garder deux threads chacun sans
    saturer le poste ; en dessous on retombe a deux, puis a un. Un poste a 4 coeurs
    reprend donc exactement le comportement historique.
    """
    plafond_cpu = max(1, coeurs // 3)
    plafond_ram = max(1, int(ram_gio // RAM_PAR_FIL_GIO))
    return max(1, min(3, plafond_cpu, plafond_ram))


def choisir(profil: str = "auto", *,
            enregistrement_en_cours: bool = False,
            sur_batterie: bool = False) -> ProfilExecution:
    """Calcule (fils, threads) pour la transcription a venir.

    `profil` vaut "auto", "rapide" ou "econome" ; toute autre valeur est traitee
    comme "auto". Le mode econome, l'enregistrement en cours et la batterie ramenent
    toujours a un seul flux : la priorite est alors de laisser le poste utilisable.
    """
    coeurs = coeurs_physiques()
    ram = ram_libre_gio()
    fils_max = _fils_max_possibles(coeurs, ram)

    if profil not in PROFILS:
        profil = "auto"

    # Un enregistrement en cours prime sur tout : le CPU sert d'abord a capturer
    # l'audio sans decrochage, et la batterie n'a pas a etre videe par du confort.
    if enregistrement_en_cours:
        return ProfilExecution(1, min(3, coeurs), "enregistrement en cours")
    if sur_batterie:
        return ProfilExecution(1, min(3, coeurs), "sur batterie")
    if profil == "econome":
        return ProfilExecution(1, min(3, coeurs), "mode econome")

    if profil == "rapide":
        fils = fils_max
        return ProfilExecution(fils, max(2, coeurs // fils), "mode rapide")

    # Mode auto : le poste au repos prend le maximum de flux, le poste deja
    # sollicite en garde un de moins pour rester reactif.
    if charge_cpu() >= SEUIL_POSTE_OCCUPE:
        fils = max(1, fils_max - 1)
        motif = "poste sollicite"
    else:
        fils = fils_max
        motif = "poste au repos"
    return ProfilExecution(fils, max(2, coeurs // fils), motif)


def description_poste() -> str:
    """Phrase decrivant le poste et ce que l'automatique en fera. Pour les reglages."""
    coeurs = coeurs_physiques()
    ram = ram_libre_gio()
    fils_max = _fils_max_possibles(coeurs, ram)
    if fils_max == 1:
        return (f"{coeurs} coeurs, {ram:.1f} Go libres — un seul flux de transcription "
                f"a la fois sur ce poste.")
    return (f"{coeurs} coeurs, {ram:.1f} Go libres — jusqu'a {fils_max} flux de "
            f"transcription en parallele.")
