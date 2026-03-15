# Surveillance Inscriptions (Lacale Watcher)

Application Windows de surveillance automatique des ouvertures d'inscriptions sur les hébergeurs francophones.

Surveille en temps réel : **la-cale.space**, **abn.lol**, **tctg.pm**, **hdf.world** + sites personnalisés.

## Fonctionnalités

- Vérification automatique à intervalle configurable (5–60 min)
- Notification Windows (bulle systray) à l'ouverture des inscriptions
- Icône dans la zone de notification (fonctionne en arrière-plan)
- Historique des états (JSON local)
- Sites personnalisés ajoutables
- Démarrage automatique avec Windows (optionnel)
- Badge β — version bêta active

## Utilisation

Télécharge le dernier `.exe` depuis la page [Releases](../../releases) et lance-le directement. Aucune installation requise.

Les fichiers de données sont créés dans le même dossier que l'exécutable :
- `check_tracker.json` — configuration
- `check_tracker_history.json` — historique des vérifications
- `check_tracker.log` — log des erreurs détaillées

## Lancer depuis le code source

```bash
pip install requests pystray pillow win10toast
python lacale_gui.py
```

Nécessite Python 3.10+, Windows uniquement.

## Compiler l'exe

```bash
pip install pyinstaller
pyinstaller lacale_gui.spec --noconfirm
```

L'exe se retrouve dans `dist/`.
