# CheckTracker

Application Windows de surveillance automatique des ouvertures d'inscriptions sur les hébergeurs francophones.

Surveille en temps réel : **la-cale.space**, **abn.lol**, **tctg.pm**, **hdf.world** + sites personnalisés.

## Fonctionnalités

- Vérification automatique à intervalle configurable (5–60 min)
- **↻ Vérifier maintenant** — vérification manuelle en un clic
- Notification Windows (bulle systray) à l'ouverture des inscriptions
- Icône dans la zone de notification (fonctionne en arrière-plan)
- Historique des états (JSON local)
- Sites personnalisés ajoutables, éditables et testables individuellement (**▶ ✎ ×**)
- Démarrage automatique avec Windows (optionnel)
- Détection automatique des mises à jour depuis GitHub Releases
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
python check_tracker.py
```

Nécessite Python 3.10+, Windows uniquement.

## Compiler l'exe

```bash
pip install pyinstaller
pyinstaller check_tracker.spec --noconfirm
```

L'exe se retrouve dans `dist/`.
