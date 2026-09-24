# Fichiers DXF d'exemple

Trois pièces simples pour tester l'import et l'imbrication sans avoir à créer un DXF soi-même :

- `equerre.dxf` — plaque 220×120 mm, 4 trous de fixation, une découpe oblongue centrale
- `flasque_ronde.dxf` — disque Ø90 mm avec un alésage central Ø20 mm
- `languette.dxf` — rectangle plein 60×25 mm, sans trou

Exemple d'upload via l'API :

```bash
curl -u <user>:<pass> -F "nom=Équerre" -F "fichier_source=@decoupe/exemples/equerre.dxf" \
  http://127.0.0.1:8000/api/v1/pieces-decoupe/
```

Les trois peuvent être imbriquées ensemble dans un même `ImbricationJob` (voir le README
principal, section "Chiffrage découpe laser / jet d'eau") pour tester l'imbrication de
plusieurs pièces différentes sur une même feuille.
