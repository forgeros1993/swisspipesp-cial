"""Tests purs de la réconciliation (core/services/reconciliation.py)."""

from __future__ import annotations

from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.domain.octroi import Octroi
from swisspipe.core.ports.adaptateur_ressource import DroitGroupe
from swisspipe.core.services.reconciliation import (
    Divergence,
    MatriceDivergente,
    comparer_droits,
    etat_desire,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)

# Ressource racine unique (niveau folder, pas d'héritage).
RES = "r1"
PARENTS: dict[str, str | None] = {RES: None}


# ---------------------------------------------------------------------------
# etat_desire
# ---------------------------------------------------------------------------


def test_etat_desire_deux_groupes_octroyes() -> None:
    octrois = {
        (RES, "g_ecriture"): Octroi.modifier(ECRITURE),
        (RES, "g_lecture"): Octroi.modifier(LECTURE),
    }
    desire = etat_desire(RES, ["g_ecriture", "g_lecture"], PARENTS, octrois)
    assert desire == frozenset(
        {DroitGroupe("g_ecriture", ECRITURE), DroitGroupe("g_lecture", LECTURE)}
    )


def test_etat_desire_groupe_refuser_omis() -> None:
    octrois = {
        (RES, "g_ok"): Octroi.modifier(ECRITURE),
        (RES, "g_refuse"): Octroi.refuser(),
    }
    desire = etat_desire(RES, ["g_ok", "g_refuse"], PARENTS, octrois)
    assert desire == frozenset({DroitGroupe("g_ok", ECRITURE)})


def test_etat_desire_groupe_sans_octroi_omis() -> None:
    octrois = {(RES, "g_ok"): Octroi.modifier(LECTURE)}
    desire = etat_desire(RES, ["g_ok", "g_absent"], PARENTS, octrois)
    assert desire == frozenset({DroitGroupe("g_ok", LECTURE)})


def test_etat_desire_heriter_sans_matrice_omis() -> None:
    # HERITER jusqu'à la racine sans matrice -> aucun droit -> omis.
    octrois = {(RES, "g"): Octroi.heriter()}
    assert etat_desire(RES, ["g"], PARENTS, octrois) == frozenset()


def test_etat_desire_vide_si_aucun_groupe() -> None:
    assert etat_desire(RES, [], PARENTS, {}) == frozenset()


# ---------------------------------------------------------------------------
# comparer_droits
# ---------------------------------------------------------------------------


def test_conforme_quand_identique() -> None:
    etat = frozenset({DroitGroupe("g1", ECRITURE), DroitGroupe("g2", LECTURE)})
    div = comparer_droits(etat, etat)
    assert div.est_conforme
    assert div == Divergence()


def test_groupe_manquant_dans_le_reel() -> None:
    # Cas upgrade : droit perdu côté Nextcloud.
    desire = frozenset({DroitGroupe("g1", ECRITURE)})
    reel: frozenset[DroitGroupe] = frozenset()
    div = comparer_droits(desire, reel)
    assert not div.est_conforme
    assert div.groupes_manquants == frozenset({DroitGroupe("g1", ECRITURE)})
    assert div.groupes_en_trop == frozenset()
    assert div.matrices_divergentes == frozenset()


def test_groupe_en_trop_dans_le_reel() -> None:
    desire: frozenset[DroitGroupe] = frozenset()
    reel = frozenset({DroitGroupe("g_fantome", LECTURE)})
    div = comparer_droits(desire, reel)
    assert div.groupes_en_trop == frozenset({DroitGroupe("g_fantome", LECTURE)})
    assert div.groupes_manquants == frozenset()
    assert not div.est_conforme


def test_matrice_divergente() -> None:
    desire = frozenset({DroitGroupe("g1", ECRITURE)})
    reel = frozenset({DroitGroupe("g1", LECTURE)})
    div = comparer_droits(desire, reel)
    assert div.matrices_divergentes == frozenset(
        {MatriceDivergente("g1", attendue=ECRITURE, reelle=LECTURE)}
    )
    assert div.groupes_manquants == frozenset()
    assert div.groupes_en_trop == frozenset()
    assert not div.est_conforme


def test_cas_mixte_manquant_en_trop_divergent() -> None:
    desire = frozenset(
        {
            DroitGroupe("g_manquant", ECRITURE),
            DroitGroupe("g_divergent", ECRITURE),
        }
    )
    reel = frozenset(
        {
            DroitGroupe("g_divergent", LECTURE),
            DroitGroupe("g_en_trop", LECTURE),
        }
    )
    div = comparer_droits(desire, reel)
    assert div.groupes_manquants == frozenset({DroitGroupe("g_manquant", ECRITURE)})
    assert div.groupes_en_trop == frozenset({DroitGroupe("g_en_trop", LECTURE)})
    assert div.matrices_divergentes == frozenset(
        {MatriceDivergente("g_divergent", attendue=ECRITURE, reelle=LECTURE)}
    )
    assert not div.est_conforme


def test_determinisme() -> None:
    desire = frozenset({DroitGroupe("g1", ECRITURE), DroitGroupe("g2", LECTURE)})
    reel = frozenset({DroitGroupe("g2", ECRITURE)})
    assert comparer_droits(desire, reel) == comparer_droits(desire, reel)


# ---------------------------------------------------------------------------
# Robustesse de l'égalité Matrice : la détection de dérive en dépend.
# ---------------------------------------------------------------------------


def test_matrice_egalite_insensible_ordre_additionnels() -> None:
    # Additionnels donnés dans deux ordres différents -> matrices ÉGALES (frozenset).
    a = Matrice(NiveauPrincipal.ECRITURE, [DroitAdditionnel.CREATION, DroitAdditionnel.CLASSEMENT])
    b = Matrice(NiveauPrincipal.ECRITURE, [DroitAdditionnel.CLASSEMENT, DroitAdditionnel.CREATION])
    assert a == b
    assert hash(a) == hash(b)


def test_comparer_droits_pas_de_fausse_derive_sur_ordre() -> None:
    # Même groupe, mêmes droits dans un ordre d'additionnels différent -> CONFORME.
    desire = frozenset(
        {
            DroitGroupe(
                "g1",
                Matrice(
                    NiveauPrincipal.ECRITURE,
                    [DroitAdditionnel.CREATION, DroitAdditionnel.CLASSEMENT],
                ),
            )
        }
    )
    reel = frozenset(
        {
            DroitGroupe(
                "g1",
                Matrice(
                    NiveauPrincipal.ECRITURE,
                    [DroitAdditionnel.CLASSEMENT, DroitAdditionnel.CREATION],
                ),
            )
        }
    )
    div = comparer_droits(desire, reel)
    assert div.est_conforme
    assert div.matrices_divergentes == frozenset()
