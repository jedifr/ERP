"""Masses du catalogue général ArcelorMittal 2020 (acier) : tubes ronds, carrés et rectangulaires formés à froid, plats, ronds et carrés pleins,
cornières, UPN, IPE, HEA, HEB. Les lignes déjà présentes **non vérifiées** reçoivent la masse du catalogue et passent en « vérifié » ; les
lignes absentes sont créées ; ce qui a été vérifié ou saisi à la main n'est jamais modifié. Les tubes carrés et rectangulaires hors catalogue,
calculés avec les rayons d'angle de la norme à chaud, sont recalculés avec ceux du formé à froid (qui retrouvent le catalogue à 2 % près).
Les sections sont enfin renumérotées par dimensions croissantes dans chaque famille."""

from django.db import migrations

from decoupe.profiles_catalogue_arcelor import LIGNES
from decoupe.profiles_data import lignes

ANCIENNES_SOURCES_TUBES = ("masse calculée, angles vifs", "masse calculée (EN 10219, angles arrondis)", "masse calculée (EN 10219, formé à froid)")
COTES_ORDRE = {
    "corniere": ("a", "e"), "corniere_inegale": ("a", "b", "e"), "upn": ("h",), "ipe": ("h",), "hea": ("h",), "heb": ("h",), "tube_carre": ("c", "e"),
    "tube_rectangulaire": ("h", "b", "e"), "tube_rond": ("d", "e"), "plat": ("l", "e"), "rond_plein": ("d",), "carre_plein": ("c",),
}


def appliquer(apps, schema_editor):
    Section = apps.get_model("technique", "ProfileSection")
    deja = set()
    for famille, designation, cotes, masse, page in LIGNES:
        deja.add(designation)
        source = f"ArcelorMittal, catalogue général 2020, p. {page}"
        existante = Section.objects.filter(designation=designation).first()
        if existante is None:
            Section.objects.create(designation=designation, famille=famille, dimensions=cotes, masse_lineique=masse, source=source, verifie=True)
        elif not existante.verifie:
            existante.masse_lineique, existante.source, existante.verifie = masse, source, True
            existante.dimensions = {**existante.dimensions, **cotes}
            existante.save(update_fields=["masse_lineique", "source", "verifie", "dimensions"])
    for famille, designation, cotes, masse, source, ordre in lignes():  # hors catalogue : recalcul des tubes avec les rayons du formé à froid
        if designation in deja:
            continue
        existante = Section.objects.filter(designation=designation, verifie=False, source__in=ANCIENNES_SOURCES_TUBES).first()
        if existante is not None:
            existante.masse_lineique, existante.source = masse, source
            existante.save(update_fields=["masse_lineique", "source"])
    for famille, cles in COTES_ORDRE.items():
        sections = list(Section.objects.filter(famille=famille))
        sections.sort(key=lambda s: (tuple(float(s.dimensions.get(c, 0)) for c in cles), s.designation))
        for rang, section in enumerate(sections, start=1):
            if section.ordre != rang:
                section.ordre = rang
                section.save(update_fields=["ordre"])


class Migration(migrations.Migration):

    dependencies = [("technique", "0022_sections_catalogue_etendu")]

    operations = [migrations.RunPython(appliquer, migrations.RunPython.noop)]
