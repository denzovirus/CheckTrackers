# CheckTracker

Application Windows de surveillance automatique des ouvertures d'inscriptions sur les hébergeurs francophones.

Surveille en temps réel : **la-cale.space**, **abn.lol**, **tctg.pm**, **hdf.world** + sites personnalisés.

## Fonctionnalités

- Vérification automatique à intervalle configurable (5–60 min)
- **↻ Vérifier maintenant** — vérification manuelle en un clic
- Notification Windows (bulle systray) à l'ouverture des inscriptions — **uniquement lors d'une transition** (fermé → ouvert)
- Icône dans la zone de notification (fonctionne en arrière-plan)
- Historique des états avec filtrage par site (JSON local)
- Sites personnalisés ajoutables, éditables et testables individuellement (**▶ ✎ ×**)
- **Compteur d'ouvertures** par site (↑N) calculé depuis l'historique
- **Timeout HTTP configurable** dans les options (3–60 s)
- Taille et position de la fenêtre mémorisées entre les sessions
- Démarrage automatique avec Windows (optionnel)
- Détection automatique des mises à jour depuis GitHub Releases
- Badge β — version bêta active

## Fonctionnement de la détection

CheckTracker charge la page d'inscription de chaque site surveillé et recherche la présence du texte `"inscriptions fermées"` dans le contenu.

- Texte **présent** → statut `FERMÉ` — aucune alerte
- Texte **absent** (et HTTP 200) → statut `OUVERT` → **notification + son**

La notification ne se déclenche qu'une seule fois par ouverture (pas de spam si le site reste ouvert).

## Utilisation

Télécharge le dernier `.exe` depuis la page [Releases](../../releases) et lance-le directement. Aucune installation requise.

Les fichiers de données sont créés dans le même dossier que l'exécutable :
- `check_tracker.json` — configuration et taille de fenêtre
- `check_tracker_history.json` — historique des vérifications
- `check_tracker.log` — log des erreurs détaillées

## Licence

Voir [LICENSE](LICENSE).
