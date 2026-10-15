"""
Lecture et gestion des mails Outlook taggués catégorie "meetnote".
Utilise win32com (COM/MAPI) — même session Outlook que outlook_cal.py.

Fonctions publiques :
  get_meetnote_emails()       → liste de dicts (id, subject, sender, date, body, entry_id)
  move_to_meetnote_ok(entry_id) → déplace le mail vers le dossier "meetnote OK"
"""
import datetime
import logging

log = logging.getLogger(__name__)

CATEGORY_NAME  = "meetnote"   # catégorie Outlook à chercher (insensible à la casse)
CATEGORY_OK    = "meetnote OK"  # catégorie après traitement


try:
    import win32com.client
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False

import datetime as _dt
import os as _os


def _dbg(msg: str):
    """Log de debug dans un fichier temporaire."""
    try:
        path = _os.path.join(_os.environ.get("TEMP", "C:\\Temp"), "meetnote_mail_debug.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def is_available() -> bool:
    return _COM_AVAILABLE


def _get_namespace():
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    outlook = win32com.client.Dispatch("Outlook.Application")
    return outlook.GetNamespace("MAPI")


def _to_naive_local(dt) -> datetime.datetime:
    try:
        return datetime.datetime(dt.year, dt.month, dt.day,
                                 dt.hour, dt.minute, dt.second)
    except Exception:
        return datetime.datetime.now()


def get_meetnote_emails(max_results: int = 500) -> list[dict]:
    """
    Retourne les mails de la boîte de réception (et sous-dossiers)
    dont la catégorie contient "meetnote" (insensible à la casse).

    Chaque dict : {
        "entry_id": str,      # identifiant MAPI unique pour move_to_meetnote_ok()
        "subject":  str,
        "sender":   str,
        "date":     datetime,
        "body":     str,      # corps texte brut
    }
    """
    if not _COM_AVAILABLE:
        _dbg("COM non disponible")
        return []

    results = []
    try:
        _dbg("get_namespace...")
        ns    = _get_namespace()
        _dbg("GetDefaultFolder(6)...")
        inbox = ns.GetDefaultFolder(6)
        _dbg(f"inbox={inbox.Name}, scan...")
        _scan_folder(inbox, results, max_results)
        _dbg(f"scan terminé: {len(results)} résultats")

    except Exception as e:
        _dbg(f"ERREUR get_meetnote_emails: {e}")
        log.debug("outlook_mail: get_meetnote_emails error (%s)", e)

    return results


def _save_attachments(item) -> list[str]:
    """
    Sauvegarde les pièces jointes d'un mail dans un dossier temp.
    Retourne la liste des chemins sauvegardés.
    Ignore les images inline (Content-ID présent, taille < 50 ko).
    """
    import os
    import tempfile
    saved = []
    try:
        atts = item.Attachments
        count = atts.Count
        if count == 0:
            return []
        tmp_dir = os.path.join(tempfile.gettempdir(), "meetnote_attachments")
        os.makedirs(tmp_dir, exist_ok=True)
        for i in range(1, count + 1):
            try:
                att = atts.Item(i)
                fname = str(att.FileName or f"attachment_{i}").strip()
                # Ignorer les images inline (petites + CID)
                try:
                    cid = str(getattr(att, "PropertyAccessor", None) and
                              att.PropertyAccessor.GetProperty(
                                  "http://schemas.microsoft.com/mapi/proptag/0x3712001E") or "")
                except Exception:
                    cid = ""
                if cid and att.Size < 51200:
                    continue
                dst = os.path.join(tmp_dir, fname)
                # Éviter les collisions de noms
                base, ext = os.path.splitext(dst)
                idx = 1
                while os.path.exists(dst):
                    dst = f"{base}_{idx}{ext}"
                    idx += 1
                att.SaveAsFile(dst)
                saved.append(dst)
            except Exception as e:
                log.debug("outlook_mail: attachment %d skip (%s)", i, e)
    except Exception as e:
        log.debug("outlook_mail: _save_attachments error (%s)", e)
    return saved


def _scan_folder(folder, results: list, max_results: int, depth: int = 0):
    """Scan récursif d'un dossier Outlook (max 2 niveaux de profondeur)."""
    if len(results) >= max_results:
        return
    if depth > 2:
        return

    try:
        # Restriction côté Outlook pour ne récupérer que les mails catégorisés
        # évite d'itérer sur toute la boîte quand elle est volumineuse
        try:
            items = folder.Items.Restrict(f"[Categories] = '{CATEGORY_NAME}'")
            _dbg(f"Restrict OK sur {folder.Name}: {items.Count} items")
        except Exception as e:
            _dbg(f"Restrict ERREUR sur {folder.Name}: {e} — fallback scan complet")
            items = folder.Items
        items.Sort("[ReceivedTime]", True)   # plus récent en premier

        for item in items:
            if len(results) >= max_results:
                break
            try:
                cls = getattr(item, "Class", None)
                cats = str(getattr(item, "Categories", "") or "")
                subj = str(getattr(item, "Subject", "") or "")
                _dbg(f"  item: class={cls} cats='{cats}' subj='{subj[:40]}'")
                # Filtrer sur la classe (43 = MailItem)
                if item.Class != 43:
                    _dbg("  → ignoré (class != 43)")
                    continue

                if CATEGORY_NAME.lower() not in cats.lower():
                    _dbg(f"  → ignoré (catégorie '{cats}' ne contient pas '{CATEGORY_NAME}')")
                    continue

                _dbg("  → save_attachments...")
                attachments = _save_attachments(item)
                _dbg("  → append result...")
                results.append({
                    "entry_id":    str(item.EntryID),
                    "subject":     str(item.Subject or "").strip(),
                    "sender":      str(getattr(item, "SenderName", "") or "").strip(),
                    "date":        _to_naive_local(item.ReceivedTime),
                    "body":        str(item.Body or "").strip(),
                    "attachments": attachments,
                })
                _dbg("  → ajouté OK")
            except Exception as e:
                _dbg(f"  → EXCEPTION item skip: {e}")
                log.debug("outlook_mail: item skip (%s)", e)
                continue
    except Exception as e:
        log.debug("outlook_mail: folder scan error %s (%s)", folder.Name, e)

    # Sous-dossiers
    if depth < 2:
        try:
            for sub in folder.Folders:
                if len(results) >= max_results:
                    break
                _scan_folder(sub, results, max_results, depth + 1)
        except Exception:
            pass


def mark_as_meetnote_ok(entry_id: str) -> bool:
    """
    Change la catégorie du mail identifié par entry_id :
    remplace "meetnote" par "meetnote OK" dans le champ Categories.
    Le mail reste dans son dossier d'origine.
    Retourne True si succès.
    """
    if not _COM_AVAILABLE:
        return False

    try:
        ns   = _get_namespace()
        item = ns.GetItemFromID(entry_id)

        cats = str(getattr(item, "Categories", "") or "")
        # Retirer "meetnote" (insensible à la casse) et ajouter "meetnote OK"
        parts = [c.strip() for c in cats.split(",") if c.strip()]
        parts = [c for c in parts if c.lower() != CATEGORY_NAME.lower()]
        if not any(c.lower() == CATEGORY_OK.lower() for c in parts):
            parts.append(CATEGORY_OK)
        item.Categories = ", ".join(parts)
        item.Save()
        log.info("outlook_mail: mark_as_meetnote_ok OK pour entry_id=%s → catégories=%s",
                 entry_id, item.Categories)
        return True

    except Exception as e:
        log.warning("outlook_mail: mark_as_meetnote_ok ERREUR (%s)", e)
        return False
