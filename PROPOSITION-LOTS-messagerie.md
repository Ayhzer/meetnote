# Proposition de lots — meetnote (plan messagerie « MailBrain plan A »)

Issue du plan validé par Alexandre le 2026-10-25. **Document de travail : rien ici n'est prenable
par `/avancer` ni par la boucle autonome tant que le lot n'a pas été recopié dans `PLAN.md` par
`/cadrer`.**

Constat : `_poll_outlook` interroge l'agenda toutes les 60 s et `_poll_meeting_end` toutes les
30 s (`meetnote-tray.py`, l. ~3545 et ~3748), via `outlook_cal` : EWS d'abord (identifiants
configurés, confirmé par Alexandre le 2026-10-25), COM en repli. Le repli COM sollicite Outlook
environ 180 fois par heure.

## L2 — MeetNote connaît mes réunions sans solliciter Outlook

### Recette utilisateur du lot
**Une journée entière de réunions : MeetNote propose le bon titre et les participants, déclenche
l'enregistrement automatiquement et m'avertit de la fin de réunion comme avant — et le journal
montre au plus quatre lectures d'agenda par heure, aucune par COM tant que l'index ou EWS
répond.**

Couvre :
- module pur `pc/source_agenda.py` (testable sans interface, sur le modèle de `perf_profile.py`) :
  cache des réunions de la journée, rechargé toutes les 15 min, au réveil du poste et sur le
  bouton 🔄 ; `reunions_en_cours(maintenant, fenetre)` et `prochaine_fin()` calculés sur le cache ;
- ordre des sources : (1) index mailbrain, lecture SQLite seule de la table `evenements`
  (`mode=ro`, `query_only`, aucun import du code mailbrain), seulement si `etat.json` dit
  « frais » ; (2) EWS ; (3) COM ;
- `_poll_outlook` et `_poll_meeting_end` lisent le cache, plus jamais Outlook directement ;
- participants pré-remplis depuis l'index (noms + SMTP) en complément du roster Teams ;
- digest agenda 15 jours vers Notion construit depuis la même source ;
- journal fichier de la source utilisée à chaque rechargement (règle du poste : ce qui tourne sans
  console écrit dans un fichier) ;
- tests : cache, bascule entre les trois sources, réunion qui commence entre deux rechargements
  (fenêtre de 30 min conservée), poste sorti de veille.

Le volet (1) s'appuie sur mailbrain L19 : tant qu'il n'est pas livré, la source (1) répond
« indisponible » et le lot reste utile (cache + EWS). Les tâches qui lisent la table `evenements`
portent le cadenas 🔒 jusqu'à la livraison de L19.

Recette humaine hors boucle : une journée de réunions réelles, journal relu le soir.
