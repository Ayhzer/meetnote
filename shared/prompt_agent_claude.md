# Prompt MeetNote — Agent Claude Desktop (MCP Notion)

Colle ce prompt dans Claude Desktop (avec MCP Notion actif) pour traiter tes transcripts automatiquement.

---

Lis toutes les pages de ma database Notion "Transcripts"
qui ont le tag "À traiter".

Pour chacune, crée une nouvelle page dans ma database
"Réunions traitées" avec :

1. **Résumé** — 5 à 8 phrases, contexte + décisions prises
2. **Décisions** — liste numérotée
3. **Plan d'actions** — tableau : Action | Responsable | Échéance
4. **Tâches Notion** — crée une tâche dans ma database "Tâches"
   pour chaque action, assignée à la bonne personne si mentionnée

Une fois la page créée, retire le tag "À traiter"
et ajoute le tag "Traité" sur la page source.
