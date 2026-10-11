"""Scripts de sauvegarde du NAS (sauvegarder-nas.sh, verifier-sauvegarde.sh, restaurer-nas.sh, exporter/importer-transfert.sh).

Docker n'existe pas ici : un faux `docker` (et un faux `sudo`) joue la base et l'application. On vérifie la logique des scripts — fichiers
produits, sommes de contrôle, rotation, chiffrement, alerte, contrôles, transfert — pas la connexion réelle à PostgreSQL du NAS."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from django.test import SimpleTestCase

RACINE = Path(__file__).resolve().parent.parent
SCRIPTS = ["sauvegarder-nas.sh", "restaurer-nas.sh", "verifier-sauvegarde.sh", "exporter-transfert.sh", "importer-transfert.sh"]

FAUX_DOCKER = r"""#!/bin/sh
echo "$@" >> "$FAKE_LOG"
[ "$1" = compose ] && shift
case "$1" in
  exec)
    shift; [ "$1" = "-T" ] && shift
    service="$1"; shift
    case "$service $1" in
      "db pg_dump")
        [ "$FAKE_FAIL" = "pgdump" ] && { echo "pg_dump: erreur simulée" >&2; exit 1; }
        head -c 3000 /dev/urandom | base64 ;;
      "web test") exit 0 ;;
      "web tar")
        case "$*" in
          *-cz*) mkdir -p "$FAKE_ROOT/media" && echo plan > "$FAKE_ROOT/media/plan.dxf" && tar -C "$FAKE_ROOT" -cz media ;;
          *) cat > /dev/null ;;
        esac ;;
      "db psql")
        all="$*"
        case "$all" in
          *information_schema*) echo "${FAKE_TABLES:-45}" ;;
          *django_migrations*) echo "${FAKE_MIGRATIONS:-120}" ;;
          *) cat > /dev/null ;;
        esac ;;
    esac ;;
esac
exit 0
"""


@unittest.skipUnless(shutil.which("openssl") and shutil.which("sha256sum"), "openssl / sha256sum absents")
class ScriptsSauvegardeTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.projet = self.tmp / "erp"
        self.projet.mkdir()
        for nom in SCRIPTS:
            shutil.copy(RACINE / nom, self.projet / nom)
        (self.projet / ".env").write_text("DB_NAME=erp_db\nDB_USER=erp_user\nDJANGO_SECRET_KEY=secret-de-test\n")
        (self.projet / "VERSION").write_text("2026.10.11.1\n")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "docker").write_text(FAUX_DOCKER)
        (bin_ / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
        for f in bin_.iterdir():
            f.chmod(0o755)
        self.log = self.tmp / "docker.log"
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE_LOG": str(self.log), "FAKE_ROOT": str(self.tmp / "app")}
        self.env.pop("ERP_SANS_COPIE_EXTERNE", None)
        self.sauvegardes = self.tmp / "erp_sauvegardes"
        self.cle = self.tmp / "cle"
        self.cle.write_text("phrase-secrete-de-test\n")

    def lancer(self, script, *args, entree=None, env=None, cwd=None):
        return subprocess.run(["sh", str(self.projet / script), *args], capture_output=True, text=True, input=entree, env={**self.env, **(env or {})}, cwd=cwd or self.projet)

    def conf(self, contenu):
        (self.projet / "sauvegarde.conf").write_text(contenu)

    def sauvegarder(self, *args, **kw):
        return self.lancer("sauvegarder-nas.sh", str(self.sauvegardes), *args, **kw)

    # ------------------------------------------------------------------------------------------ sauvegarde
    def test_sauvegarde_produit_fichiers_somme_et_etat(self):
        r = self.sauvegarder()
        self.assertEqual(r.returncode, 0, r.stderr)
        bases = sorted(self.sauvegardes.glob("erp_base_*.sql.gz"))
        self.assertEqual(len(bases), 1)
        self.assertEqual(len(list(self.sauvegardes.glob("erp_fichiers_*.tar.gz"))), 1)
        sommes = (self.sauvegardes / "SHA256SUMS").read_text()
        self.assertIn(bases[0].name, sommes)
        etat = (self.sauvegardes / "derniere_sauvegarde.txt").read_text()
        self.assertIn(f"base={bases[0].name}", etat)
        self.assertIn("copie_externe=non", etat)
        self.assertIn("Pensez à copier", r.stdout)

    def test_rotation_ne_garde_que_les_dernieres(self):
        for _ in range(3):
            self.assertEqual(self.sauvegarder("2").returncode, 0)
            time.sleep(1.1)
        self.assertEqual(len(list(self.sauvegardes.glob("erp_base_*.sql.gz"))), 2)
        sommes = (self.sauvegardes / "SHA256SUMS").read_text().strip().splitlines()
        self.assertEqual(len(sommes), 4)  # 2 bases + 2 archives de fichiers : les sommes des fichiers supprimés disparaissent

    def test_copie_externe_chiffree_et_dechiffrable(self):
        externe = self.tmp / "usb"
        self.conf(f'COPIE_EXTERNE="{externe}"\nCLE_CHIFFREMENT="{self.cle}"\n')
        r = self.sauvegarder()
        self.assertEqual(r.returncode, 0, r.stderr)
        copies = sorted(p.name for p in externe.iterdir())
        self.assertTrue(all(n.endswith(".enc") for n in copies), copies)
        self.assertEqual(len(copies), 2)
        base = next(self.sauvegardes.glob("erp_base_*.sql.gz"))
        chiffree = next(externe.glob("erp_base_*.enc"))
        self.assertNotEqual(chiffree.read_bytes(), base.read_bytes())
        clair = self.tmp / "clair.sql.gz"
        d = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-in", str(chiffree), "-out", str(clair), "-pass", f"file:{self.cle}"], capture_output=True)
        self.assertEqual(d.returncode, 0, d.stderr)
        self.assertEqual(clair.read_bytes(), base.read_bytes())
        self.assertIn("chiffree=oui", (self.sauvegardes / "derniere_sauvegarde.txt").read_text())
        self.assertEqual([p.name for p in self.sauvegardes.glob(".externe.*")], [])  # temporaire nettoyé

    def test_copie_externe_non_chiffree_prevenue_et_rotation_externe(self):
        externe = self.tmp / "usb"
        self.conf(f'COPIE_EXTERNE="{externe}"\nGARDER_EXTERNE=1\n')
        r = self.sauvegarder()
        self.assertIn("NON chiffrée", r.stdout)
        time.sleep(1.1)
        self.sauvegarder()
        self.assertEqual(len(list(externe.glob("erp_base_*"))), 1)

    def test_echec_alerte_et_code_de_sortie(self):
        marque = self.tmp / "alerte.txt"
        self.conf(f'ALERTE_COMMANDE=\'echo "$MESSAGE_ALERTE" > {marque}\'\n')
        r = self.sauvegarder(env={"FAKE_FAIL": "pgdump"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ÉCHOUÉE", r.stderr)
        self.assertIn("a échoué", marque.read_text())
        self.assertFalse((self.sauvegardes / "derniere_sauvegarde.txt").exists())  # pas d'état « réussi » après un échec

    def test_pas_d_alerte_quand_tout_va_bien(self):
        marque = self.tmp / "alerte.txt"
        self.conf(f'ALERTE_COMMANDE=\'echo oups > {marque}\'\n')
        self.assertEqual(self.sauvegarder().returncode, 0)
        self.assertFalse(marque.exists())

    def test_cle_introuvable_est_une_erreur(self):
        self.conf(f'COPIE_EXTERNE="{self.tmp / "usb"}"\nCLE_CHIFFREMENT="{self.tmp / "absente"}"\n')
        r = self.sauvegarder()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Clé de chiffrement introuvable", r.stderr)

    # ------------------------------------------------------------------------------------------ vérification
    def test_verification_ok_et_base_temporaire_supprimee(self):
        self.sauvegarder()
        r = self.lancer("verifier-sauvegarde.sh", env={"ERP_SAUVEGARDES": str(self.sauvegardes)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Sauvegarde utilisable", r.stdout)
        self.assertIn("somme de contrôle : correcte", r.stdout)
        journal = self.log.read_text()
        self.assertIn("CREATE DATABASE", journal)
        self.assertEqual(journal.count("DROP DATABASE"), 1)

    def test_verification_detecte_un_fichier_abime(self):
        self.sauvegarder()
        base = next(self.sauvegardes.glob("erp_base_*.sql.gz"))
        base.write_bytes(base.read_bytes() + b"x")  # ajoute un octet : la somme ne correspond plus
        r = self.lancer("verifier-sauvegarde.sh", "--sans-restauration", env={"ERP_SAUVEGARDES": str(self.sauvegardes)})
        self.assertNotEqual(r.returncode, 0)

    def test_verification_age_maximum(self):
        self.sauvegarder()
        base = next(self.sauvegardes.glob("erp_base_*.sql.gz"))
        env = {"ERP_SAUVEGARDES": str(self.sauvegardes)}
        self.assertEqual(self.lancer("verifier-sauvegarde.sh", "--age-max", "2", "--sans-restauration", env=env).returncode, 0)
        vieux = time.time() - 10 * 86400
        os.utime(base, (vieux, vieux))
        r = self.lancer("verifier-sauvegarde.sh", "--age-max", "2", "--sans-restauration", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("10 jour", r.stderr)

    def test_verification_base_vide_refusee(self):
        self.sauvegarder()
        r = self.lancer("verifier-sauvegarde.sh", env={"ERP_SAUVEGARDES": str(self.sauvegardes), "FAKE_TABLES": "3"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("vide ou incomplète", r.stderr)

    def test_verification_d_une_copie_chiffree(self):
        externe = self.tmp / "usb"
        self.conf(f'COPIE_EXTERNE="{externe}"\nCLE_CHIFFREMENT="{self.cle}"\n')
        self.sauvegarder()
        chiffree = next(externe.glob("erp_base_*.enc"))
        r = self.lancer("verifier-sauvegarde.sh", str(chiffree))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("déchiffrement : correct", r.stdout)
        self.cle.write_text("autre-phrase\n")  # mauvaise phrase secrète
        self.assertNotEqual(self.lancer("verifier-sauvegarde.sh", "--sans-restauration", str(chiffree)).returncode, 0)

    # ------------------------------------------------------------------------------------------ restauration
    def test_restauration_nouveau_nas_sans_sauvegarde_de_securite(self):
        externe = self.tmp / "usb"
        self.conf(f'COPIE_EXTERNE="{externe}"\nCLE_CHIFFREMENT="{self.cle}"\n')
        self.sauvegarder()
        self.log.write_text("")
        base = next(externe.glob("erp_base_*.enc"))
        media = next(externe.glob("erp_fichiers_*.enc"))
        r = self.lancer("restaurer-nas.sh", "--nouveau-nas", str(base), str(media), entree="RESTAURER\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        journal = self.log.read_text()
        self.assertIn("DROP DATABASE", journal)
        self.assertNotIn("pg_dump", journal)  # installation neuve : pas de sauvegarde de sécurité
        self.assertNotIn("avant_restauration", r.stdout)

    def test_restauration_normale_fait_une_sauvegarde_de_securite_et_demande_confirmation(self):
        self.sauvegarder()
        base = next(self.sauvegardes.glob("erp_base_*.sql.gz"))
        r = self.lancer("restaurer-nas.sh", str(base), entree="non\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("DROP DATABASE", self.log.read_text())
        r = self.lancer("restaurer-nas.sh", str(base), entree="RESTAURER\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "erp_sauvegardes" / "avant_restauration").exists() or (Path(str(self.projet) + "_sauvegardes") / "avant_restauration").exists())

    def test_restauration_fichier_chiffre_sans_cle(self):
        externe = self.tmp / "usb"
        self.conf(f'COPIE_EXTERNE="{externe}"\nCLE_CHIFFREMENT="{self.cle}"\n')
        self.sauvegarder()
        (self.projet / "sauvegarde.conf").unlink()
        r = self.lancer("restaurer-nas.sh", "--nouveau-nas", str(next(externe.glob("erp_base_*.enc"))), entree="RESTAURER\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CLE_CHIFFREMENT", r.stderr)

    # ------------------------------------------------------------------------------------------ transfert
    def test_transfert_complet_avec_env_chiffre(self):
        self.conf(f'CLE_CHIFFREMENT="{self.cle}"\n')
        dossier = self.tmp / "transferts"
        r = self.lancer("exporter-transfert.sh", "--avec-env", str(dossier))
        self.assertEqual(r.returncode, 0, r.stderr)
        archives = list(dossier.iterdir())
        self.assertEqual(len(archives), 1)
        self.assertTrue(archives[0].name.endswith(".tar.gz.enc"))
        self.assertNotIn(b"secret-de-test", archives[0].read_bytes())  # le .env ne voyage pas en clair
        # Nouveau NAS : projet neuf (sans .env), même phrase secrète
        neuf = self.tmp / "nouveau" / "erp"
        neuf.mkdir(parents=True)
        for nom in SCRIPTS:
            shutil.copy(RACINE / nom, neuf / nom)
        (neuf / "VERSION").write_text("2026.10.12.0\n")
        (neuf / "sauvegarde.conf").write_text(f'CLE_CHIFFREMENT="{self.cle}"\n')
        self.log.write_text("")
        r = subprocess.run(["sh", str(neuf / "importer-transfert.sh"), str(archives[0])], capture_output=True, text=True, env=self.env, cwd=neuf)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("secret-de-test", (neuf / ".env").read_text())
        self.assertEqual(oct((neuf / ".env").stat().st_mode)[-3:], "600")
        self.assertIn("Version d'origine : 2026.10.11.1", r.stdout)
        journal = self.log.read_text()
        self.assertIn("up -d db", journal)
        self.assertIn("DROP DATABASE", journal)
        self.assertEqual(list(neuf.parent.glob("erp_import.*")), [])  # temporaires nettoyés

    def test_transfert_sans_env_et_refus_de_l_env_en_clair(self):
        dossier = self.tmp / "transferts"
        r = self.lancer("exporter-transfert.sh", "--avec-env", str(dossier))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ne voyage jamais en clair", r.stderr)
        r = self.lancer("exporter-transfert.sh", str(dossier))
        self.assertEqual(r.returncode, 0, r.stderr)
        archive = next(dossier.iterdir())
        self.assertTrue(archive.name.endswith(".tar.gz"))
        liste = subprocess.run(["tar", "-tzf", str(archive)], capture_output=True, text=True).stdout
        self.assertIn("erp_transfert/LISEZMOI.txt", liste)
        self.assertNotIn("erp_transfert/env", liste)

    def test_mise_a_jour_conserve_la_configuration_de_sauvegarde(self):
        self.assertIn("sauvegarde.conf", (RACINE / "update-nas.sh").read_text())
