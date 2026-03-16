CheckTracker v0.4 — β

Surveillance automatique des ouvertures d'inscriptions sur les hébergeurs francophones.
🔄 Réécriture complète — Python → C# WinForms

Cette version abandonne entièrement Python pour une application native Windows écrite en C# .NET Framework 4.7.2. Aucun runtime Python requis, aucune dépendance externe. Un seul .exe autonome.

Nouveautés v0.4

Application native Windows :

• Réécriture complète de Python vers C# WinForms
• Exécutable autonome — aucune installation, aucun Python requis
• Interface plus réactive et plus stable

Fonctionnalités :

• Intervalle de vérification en minutes (5–60 min), jamais moins de 5 min
• Variation aléatoire ±15 secondes pour éviter la détection anti-bot
• Filtre console en temps réel : Tous / OUVERT / FERMÉ / ERREUR
• Historique complet avec URLs des sites vérifiés
• Vérification de mise à jour automatique au démarrage via GitHub Releases
• Mise à jour automatique : téléchargement + remplacement de l'exe + redémarrage en un clic
• Badge de version unifié β 0.4 ✓ / β 0.4 ↑ v0.x

Sites surveillés par défaut :
• la-cale.space
• abn.lol
• tctg.pm
• hdf.world
Sites personnalisés ajoutables / modifiables / supprimables

Utilisation :

Télécharge CheckTracker.exe et lance-le directement. Aucune installation requise.
⚠️ Version bêta — des bugs peuvent subsister. N'hésite pas à ouvrir une issue.
