"""English names and short stories for the 88 IAU constellations (shown in the target panel and on the sky map).

Each entry is ``id: (English name, story)``. The stories are deliberately short and stick to well-known tradition and
facts: where a figure's origin is a mapmaker's invention rather than a myth, the text says so. Serpens is one entry
(the catalog draws its two halves, Caput and Cauda, under the same id).
"""

from __future__ import annotations

LORE: dict[str, tuple[str, str]] = {
    "And": ("Andromeda", "The princess chained to a rock as an offering to the sea monster Cetus, rescued by Perseus. Home of the Andromeda Galaxy (M31), the most distant thing most people can see without a telescope."),
    "Ant": ("Air Pump", "A faint constellation made up by Nicolas-Louis de Lacaille in the 1750s to honour the air pump. No myth, no bright stars."),
    "Aps": ("Bird of Paradise", "A small southern constellation introduced by Dutch navigators in the 1590s, near the south celestial pole."),
    "Aqr": ("Water Bearer", "Often Ganymede, the cupbearer of the gods, pouring water toward the Southern Fish. Holds the Helix Nebula and the globular cluster M2."),
    "Aql": ("Eagle", "The eagle that carried Zeus's thunderbolts. Its brightest star, Altair, is one corner of the Summer Triangle."),
    "Ara": ("Altar", "The altar on which the gods swore an oath before their war against the Titans. It lies low in the south, beside the tail of Scorpius."),
    "Ari": ("Ram", "The ram with the Golden Fleece that Jason's Argonauts sought. A small, faint zodiac constellation; its brightest star is Hamal."),
    "Aur": ("Charioteer", "Often Erichthonius of Athens, said to have invented the four-horse chariot. Capella is one of the brightest stars in the sky; the open clusters M36, M37 and M38 are here."),
    "Boo": ("Herdsman", "The herdsman or ploughman driving the bears around the pole. Arcturus, its brightest star, is the brightest of the northern sky."),
    "Cae": ("Engraving Tool", "A faint constellation made up by Lacaille in the 1750s to honour the engraver's chisel."),
    "Cam": ("Giraffe", "A faint northern constellation added in the early 1600s, near the north celestial pole. It has no myth."),
    "Cnc": ("Crab", "The crab Hera sent to nip Heracles's foot during his fight with the Hydra; he crushed it. Its heart is the Beehive Cluster, M44."),
    "CVn": ("Hunting Dogs", "Two dogs on a leash held by Boötes, added by Hevelius in 1687. Home of the globular cluster M3 and the Whirlpool Galaxy, M51."),
    "CMa": ("Greater Dog", "Orion's larger hunting dog. It holds Sirius, the brightest star in the night sky."),
    "CMi": ("Lesser Dog", "Orion's smaller hound. Procyon is its brightest star and part of the Winter Triangle."),
    "Cap": ("Sea-goat", "Half goat, half fish: Pan, fleeing the monster Typhon, jumped into the Nile and only half of him made it. A faint zodiac constellation."),
    "Car": ("Keel", "The keel of Argo Navis, Jason's ship, which Lacaille split into Carina, Puppis and Vela. Canopus is the second brightest star in the sky; the Carina Nebula is here."),
    "Cas": ("Queen Cassiopeia", "The vain queen who boasted of her beauty and brought Poseidon's monster on her kingdom. Its W shape circles the north pole all night from northern Europe."),
    "Cen": ("Centaur", "The centaur, often the wise Chiron. Alpha Centauri is the nearest star system to the Sun, and Omega Centauri the largest globular cluster."),
    "Cep": ("King Cepheus", "The king of Aethiopia, husband of Cassiopeia and father of Andromeda. Delta Cephei gave its name to the Cepheid variable stars used to measure distances."),
    "Cet": ("Whale", "Cetus, the sea monster sent to ravage Aethiopia before Perseus dealt with it. Mira, the first variable star ever noticed, is its heart."),
    "Cha": ("Chameleon", "A small constellation near the south celestial pole, introduced by Dutch navigators in the 1590s."),
    "Cir": ("Drawing Compass", "A tiny constellation made up by Lacaille in the 1750s, next to Alpha Centauri in the Milky Way."),
    "Col": ("Dove", "Named by Plancius in 1592; linked either to Noah's dove or to the dove that guided the Argonauts through the clashing rocks."),
    "Com": ("Berenice's Hair", "The hair Queen Berenice II of Egypt cut off as an offering for her husband's safe return from war. Its loose star cluster is easy to spot, and the region is rich in galaxies."),
    "CrA": ("Southern Crown", "A wreath of stars south of Sagittarius, known since Ptolemy. It mirrors the Northern Crown."),
    "CrB": ("Northern Crown", "The crown Dionysus gave Ariadne. A neat semicircle with Alphecca at its jewel; T Coronae Borealis is a recurrent nova."),
    "Crv": ("Crow", "Apollo's crow, sent to fetch water in a cup, delayed by a fig tree and then blamed a snake. All three, crow, cup and snake, were set in the sky."),
    "Crt": ("Cup", "Apollo's cup, which the crow was sent to fill. It sits on the back of Hydra, beside Corvus."),
    "Cru": ("Southern Cross", "The smallest of the 88 constellations, and a guide to the south. Its long axis points toward the south celestial pole; it never rises from northern Europe."),
    "Cyg": ("Swan", "Zeus disguised as a swan, or Orpheus set beside his lyre. Deneb marks its tail, and the double star Albireo its beak. The long cross lies along the Milky Way."),
    "Del": ("Dolphin", "The dolphin that found the sea nymph Amphitrite for Poseidon. A compact, easy diamond of stars."),
    "Dor": ("Swordfish", "A southern constellation from the 1590s. Most of the Large Magellanic Cloud, with the Tarantula Nebula, lies in it."),
    "Dra": ("Dragon", "Ladon, the dragon that guarded the golden apples until Heracles killed it. It winds between the two Bears; Thuban was the pole star in ancient Egypt."),
    "Equ": ("Little Horse", "The foal Celeritas, brother of Pegasus. The second smallest constellation."),
    "Eri": ("River", "A celestial river, linked to the Po, the Nile and the river Phaethon fell into. It ends at the bright star Achernar in the far south."),
    "For": ("Furnace", "A faint constellation made up by Lacaille in the 1750s for the chemist's furnace. It holds a rich cluster of galaxies."),
    "Gem": ("Twins", "The brothers Castor and Pollux, who sailed with the Argonauts. The Geminid meteor shower in December appears to come from here."),
    "Gru": ("Crane", "A southern constellation from the 1590s, a long-legged bird. Alnair is its brightest star."),
    "Her": ("Hercules", "The hero of the twelve labours, shown kneeling. The globular cluster M13 is the showpiece of the northern summer sky."),
    "Hor": ("Clock", "A faint constellation made up by Lacaille in the 1750s to honour the pendulum clock."),
    "Hya": ("Water Snake", "The many-headed Hydra that Heracles fought. It is the longest and largest constellation of all."),
    "Hyi": ("Lesser Water Snake", "A southern water snake from the 1590s, a smaller companion to Hydra."),
    "Ind": ("Indian", "A southern constellation from the 1590s, standing for a native of the East Indies."),
    "Lac": ("Lizard", "A small zigzag of stars between Cygnus and Andromeda, added by Hevelius in 1687."),
    "Leo": ("Lion", "The Nemean Lion that Heracles strangled. The Sickle of stars outlines its head and mane, ending at Regulus. The Leonid meteors come from here in November."),
    "LMi": ("Lesser Lion", "A faint constellation added by Hevelius in 1687 to fill the gap between Leo and the Great Bear."),
    "Lep": ("Hare", "The hare at Orion's feet, forever chased by his two dogs."),
    "Lib": ("Scales", "The scales of justice, held by Virgo as Astraea; once the claws of Scorpius. A faint zodiac constellation."),
    "Lup": ("Wolf", "An old figure, a wild beast speared by the Centaur. It lies in the Milky Way, south of Scorpius."),
    "Lyn": ("Lynx", "Hevelius named it in 1687 for being so faint that you need the eyes of a lynx to see it."),
    "Lyr": ("Lyre", "The lyre of Orpheus, made by Hermes. Vega, the fifth brightest star in the sky, anchors the Summer Triangle; the Ring Nebula, M57, is here."),
    "Men": ("Table Mountain", "A faint southern constellation, named by Lacaille for Table Mountain in South Africa, where he observed. The only one named after a real place."),
    "Mic": ("Microscope", "A faint constellation made up by Lacaille in the 1750s to honour the microscope."),
    "Mon": ("Unicorn", "A faint constellation of the early 1600s in the winter Milky Way. The Rosette Nebula is here."),
    "Mus": ("Fly", "A small southern constellation from the 1590s, near the Southern Cross."),
    "Nor": ("Carpenter's Square", "A small constellation made up by Lacaille in the 1750s, in the Milky Way."),
    "Oct": ("Octant", "Lacaille named it for the octant, a navigation instrument. It holds the south celestial pole; faint Sigma Octantis is the southern pole star."),
    "Oph": ("Serpent Bearer", "Asclepius, the healer, holding the serpent. Barnard's Star, a famous nearby red dwarf, is here, and the Sun passes through it each December."),
    "Ori": ("Orion", "The great hunter. Betelgeuse and Rigel mark his shoulder and foot, three stars his belt, and the Orion Nebula (M42) his sword. Scorpius, who killed him, rises as he sets."),
    "Pav": ("Peacock", "A southern constellation from the 1590s, the bird of the goddess Hera."),
    "Peg": ("Pegasus", "The winged horse born from Medusa's blood. Its Great Square is a guide to autumn."),
    "Per": ("Perseus", "The hero who beheaded Medusa and rescued Andromeda. Algol, the Demon Star, dims every three days; the Double Cluster is nearby, and the Perseid meteors peak in August."),
    "Phe": ("Phoenix", "The mythical bird that rises from its own ashes, added to the southern sky in the 1590s."),
    "Pic": ("Painter's Easel", "A faint constellation made up by Lacaille in the 1750s. A dust disc has been imaged around its star Beta Pictoris."),
    "Psc": ("Fishes", "Aphrodite and Eros turned into fish, tied together by a cord, to escape the monster Typhon. A faint zodiac constellation."),
    "PsA": ("Southern Fish", "A fish drinking the water poured by Aquarius. Fomalhaut, its brightest star, is the loneliest bright star of the autumn sky."),
    "Pup": ("Stern", "The stern of Argo Navis, Jason's ship, now one of the three parts of the old constellation. It lies in the Milky Way and holds many open clusters."),
    "Pyx": ("Mariner's Compass", "Lacaille's compass, a faint constellation that sits where the mast of Argo once stood."),
    "Ret": ("Reticle", "A small southern constellation named for the cross-hair grid in a telescope eyepiece."),
    "Sge": ("Arrow", "A tiny arrow, said to be Heracles's or Cupid's. The third smallest constellation."),
    "Sgr": ("Archer", "A centaur archer. The centre of the Milky Way lies behind it, and its stars form the Teapot asterism."),
    "Sco": ("Scorpion", "The scorpion that killed Orion. Antares, a red supergiant, is its heart; the tail curls deep into the Milky Way."),
    "Scl": ("Sculptor", "A faint constellation made up by Lacaille in the 1750s. It lies toward the south galactic pole, where the view is clear of the Milky Way."),
    "Sct": ("Shield", "Named by Hevelius in 1684 for the shield of King John III Sobieski of Poland. The Wild Duck Cluster, M11, is here."),
    "Ser": ("Serpent", "The serpent held by Ophiuchus; the only constellation in two parts, Caput (head) and Cauda (tail). The Eagle Nebula, M16, is in the tail."),
    "Sex": ("Sextant", "A faint constellation added by Hevelius in 1687 for his astronomical sextant."),
    "Tau": ("Bull", "Zeus disguised as a bull to carry off Europa. It holds red Aldebaran, the Hyades, and the Pleiades."),
    "Tel": ("Telescope", "A faint constellation made up by Lacaille in the 1750s to honour the telescope."),
    "Tri": ("Triangle", "A small triangle between Andromeda and Aries. It holds the Triangulum Galaxy, M33."),
    "TrA": ("Southern Triangle", "A bright little triangle in the far south, introduced in the 1590s."),
    "Tuc": ("Toucan", "A southern constellation from the 1590s. It holds the Small Magellanic Cloud and the globular cluster 47 Tucanae."),
    "UMa": ("Great Bear", "Callisto, turned into a bear by Hera. The Big Dipper is part of it; its two end stars point to Polaris."),
    "UMi": ("Little Bear", "The cub of the Great Bear. Polaris, the North Star, is at the end of its tail."),
    "Vel": ("Sails", "The sails of Argo Navis, Jason's ship. The Vela Pulsar, left by a supernova about 11,000 years ago, is in it."),
    "Vir": ("Maiden", "A harvest goddess, Demeter or Astraea. Spica is its brightest star; the Virgo Galaxy Cluster lies behind it."),
    "Vol": ("Flying Fish", "A small southern constellation from the 1590s, near the Large Magellanic Cloud."),
    "Vul": ("Fox", "A faint constellation added by Hevelius in 1687. The Dumbbell Nebula, M27, is here."),
}


def english_name(con_id: str) -> str:
    """English name of a constellation id, or the id itself when unknown."""
    entry = LORE.get(con_id)
    return entry[0] if entry else con_id


def story(con_id: str) -> str | None:
    entry = LORE.get(con_id)
    return entry[1] if entry else None
