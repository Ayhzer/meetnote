# Guide d'installation de MeetNote

MeetNote enregistre vos réunions Outlook, les transcrit sur votre PC et envoie le texte dans Notion. Il vous faut Windows 10 ou 11 et un compte Notion (l'offre gratuite suffit). Comptez 15 minutes.

## 1. Télécharger et installer

1. Téléchargez l'installeur `MeetNote-<version>-installation.exe` à l'adresse que l'on vous a communiquée.
2. Double-cliquez dessus. Windows affiche une alerte bleue : cliquez sur « Informations complémentaires », puis sur « Exécuter quand même ». Cette alerte est normale : l'installeur est signé avec un certificat auto-signé, que Windows ne connaît pas.
3. Suivez l'installation. Elle se fait pour votre seul compte, sans droits d'administrateur. Laissez cochée l'option « Démarrer avec Windows », puis cliquez sur « Lancer MeetNote ».

![Alerte SmartScreen : le lien Informations complémentaires, puis le bouton Exécuter quand même](docs/captures/smartscreen.png)

## 2. Préparer Notion

1. Dans votre navigateur, ouvrez notion.so/my-integrations. Créez une nouvelle intégration, puis copiez son jeton secret.
2. Dans Notion, créez une page vide qui accueillera vos transcriptions, par exemple `MeetNote`.
3. Sur cette page, ouvrez le menu ··· → Connexions et ajoutez votre intégration. Sans ce partage, MeetNote ne voit pas la page.
4. Copiez le lien de la page (menu ··· → Copier le lien).

MeetNote crée lui-même la base de données « Transcriptions » sous cette page. Vous n'avez rien à construire. Si vous avez déjà une base MeetNote, collez son lien à la place : l'assistant vérifie ses colonnes.

![Page de l'intégration Notion avec le jeton secret à copier](docs/captures/notion-integration.png)

## 3. Suivre l'assistant de premier lancement

L'assistant s'ouvre tout seul au premier lancement. Vous pouvez le rouvrir plus tard avec « Relancer l'assistant » (Settings, onglet « Général »). Quatre écrans, avec « Suivant » entre chacun :

1. **Connexion à Notion.** Collez le jeton dans « Jeton de l'intégration » et le lien dans « Lien de la page parente ou de la base existante ». MeetNote crée la base, puis envoie une page de test. Vous pouvez la supprimer ensuite.
2. **Votre agenda.** Rien à saisir : l'écran indique seulement si Outlook classique ou un compte Exchange est détecté.
3. **Réunions à ne pas enregistrer.** La liste proposée convient pour démarrer (voir la section 4).
4. **Test du son.** Cliquez sur « Lancer le test » : parlez 5 secondes, puis un son d'essai est joué. Le micro doit capter votre voix pour pouvoir cliquer sur « Terminer ».

![Premier écran de l'assistant : jeton et lien de la page Notion](docs/captures/assistant-notion.png)
![Quatrième écran de l'assistant : test du micro et du son du PC](docs/captures/assistant-son.png)

Après « Terminer », MeetNote choisit tout seul le modèle de transcription Whisper : `small` si votre PC a moins de 8 Gio de mémoire, `medium` sinon. Il le télécharge alors (connexion Internet nécessaire). Tant qu'il n'est pas arrivé, les enregistrements attendent dans la file et sont transcrits ensuite.

## 4. Régler les exclusions

Une réunion dont le titre contient l'un de ces mots n'est pas enregistrée automatiquement. Pour les modifier : ouvrez « Settings », onglet « Outlook », zone « Exclusions auto-start », un mot par ligne, puis cliquez sur « SAVE CONFIGURATION ».

Liste par défaut : `todo`, `off`, `perso`, `privé`, `prive`, `déjeuner`, `dejeuner`, `trajet`, `ne pas enregistrer`. Le mot est cherché dans tout le titre, sans tenir compte des majuscules : `off` exclut aussi un titre contenant `Kick-off`. Ajustez selon vos habitudes.

![Réglages, onglet Outlook : la zone des exclusions](docs/captures/exclusions.png)

## 5. Ce que fait MeetNote

- **Enregistrement automatique.** Une réunion de votre agenda démarre l'enregistrement (micro et son du PC) à son heure, sauf si elle est exclue. Vous pouvez aussi l'arrêter ou le lancer à la main depuis l'icône de la barre des tâches.
- **Transcription locale.** Le son reste sur votre PC. La transcription est faite par Whisper, sans envoi du son sur Internet.
- **Envoi dans Notion.** Le texte arrive dans la base « Transcriptions », avec la date, la durée et le statut « À traiter ». Une copie est gardée dans Documents\MeetNote.
- **Information des participants.** Pendant l'enregistrement, cochez « J'ai informé les participants ». L'heure de la confirmation est inscrite en tête du transcript local, pas dans Notion.
- **Suppression automatique.** L'audio est supprimé 30 jours après sa transcription, les transcriptions 6 mois après la réunion, vers la Corbeille de Windows. Réglez ces durées dans Settings, onglet « Général », section « Conservation des fichiers », puis « SAVE CONFIGURATION ».

Facultatif : le fichier `shared/prompt_agent_claude.md` contient un prompt pour que Claude rédige les comptes rendus des transcripts à traiter.

## Limites

- Windows uniquement.
- L'agenda doit être Outlook classique ou un compte Exchange (EWS). Microsoft a annoncé la fin d'EWS sur Exchange Online.
- L'import des transcripts Teams demande que l'administrateur Microsoft 365 inscrive l'application au préalable.
- Le modèle `medium` est lent sur une petite machine ; choisissez alors `small` dans la liste « Whisper Model ».
- Ne pas utiliser pour des réunions contenant des données de santé sans avis du DPO.
