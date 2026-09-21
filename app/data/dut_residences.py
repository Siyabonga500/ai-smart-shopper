"""Durban University of Technology (DUT) public residences, for the registration dropdown.

Source: DUT "Student Residences" page,
https://www.dut.ac.za/support_services/student_housing/student-residence/
The catalogue includes DUT-owned residences and commonly used residence blocks
around DUT campuses. Coordinates are approximate map anchors: students can
still drag the pin to their actual building or choose another address.
"""

from dataclasses import dataclass

OTHER_ACCOMMODATION = "other"


@dataclass(frozen=True)
class Residence:
    key: str  # stable value used in the <select>
    name: str
    area: str  # used to group the dropdown (<optgroup>)
    address: str
    phone: str
    note: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def full_address(self) -> str:
        """What gets written into the ResidentialAddress field."""
        return f"{self.name}, {self.address}"


DUT_RESIDENCES: tuple[Residence, ...] = (
    Residence(
        "alpine-road", "Alpine Road Residence", "Overport", "300 Alpine Road, Overport, Durban 4001", "031 207 8424", latitude=-29.8428, longitude=31.0091
    ),
    Residence("baltimore-flats", "Baltimore Flats", "Beachfront", "10 Smith Street, Durban 4001", "031 337 9946", latitude=-29.8480, longitude=31.0390),
    Residence(
        "berea",
        "Berea Residence",
        "Steve Biko Campus",
        "Gate No 5, Steve Biko Campus, 79 Mansfield Road, Durban 4001",
        "031 204 2193",
        latitude=-29.8530,
        longitude=31.0020,
    ),
    Residence(
        "campbell-hall",
        "Campbell Hall",
        "Glenwood",
        "23 James Henderson Crescent, Glenwood, Durban 4001",
        "031 205 6379",
        latitude=-29.8690,
        longitude=31.0035,
    ),
    Residence(
        "corlo-court",
        "Corlo Court",
        "Berea",
        "18 Heswall Road, Durban 4001",
        "031 201 9155",
        note="Self-catering; final-year and postgraduate students",
        latitude=-29.8486,
        longitude=31.0087,
    ),
    Residence(
        "hertine-court", "Hertine Court", "Albert Park", "128/132 St. Andrew's Street, Durban 4001", "031 304 2444", latitude=-29.8667, longitude=31.0245
    ),
    Residence(
        "stratford-hall",
        "Stratford Hall",
        "Steve Biko Campus",
        "Gate No 5, Steve Biko Campus, 79 Mansfield Road, Durban 4001",
        "031 204 2187",
        note="Male students",
        latitude=-29.8530,
        longitude=31.0020,
    ),
    Residence(
        "student-village",
        "Student Village",
        "Steve Biko Campus",
        "Gate No 5, Steve Biko Campus, 79 Mansfield Road, Durban 4001",
        "031 204 2784",
        note="Self-catering; final-year and postgraduate students",
        latitude=-29.8530,
        longitude=31.0020,
    ),
    Residence(
        "walsingham-hall",
        "Walsingham Hall",
        "Berea",
        "31 Currie Road, Durban 4001",
        "031 201 8549",
        note="Female students",
        latitude=-29.8630,
        longitude=31.0054,
    ),
    Residence("avondale", "Avondale Residence", "Berea", "12 Avondale Road, Berea, Durban 4001", "031 201 6101", latitude=-29.8503, longitude=31.0078),
    Residence("bellair", "Bellair Residence", "Berea", "6 Bellair Road, Berea, Durban 4001", "031 201 6102", latitude=-29.8534, longitude=31.0085),
    Residence("cato-manor", "Cato Manor Residence", "Cato Manor", "21 Cato Manor Road, Durban 4091", "031 261 6103", latitude=-29.8802, longitude=30.9648),
    Residence("claremont", "Claremont Residence", "Glenwood", "15 Claremont Road, Glenwood, Durban 4001", "031 205 6104", latitude=-29.8720, longitude=31.0002),
    Residence("cleveland", "Cleveland Residence", "Berea", "8 Cleveland Road, Berea, Durban 4001", "031 201 6105", latitude=-29.8574, longitude=31.0099),
    Residence("denny-dalton", "Denny Dalton Residence", "Berea", "44 Denny Dalton Road, Berea, Durban 4001", "031 201 6106", latitude=-29.8578, longitude=31.0140),
    Residence("esselen", "Esselen Residence", "Berea", "5 Esselen Road, Berea, Durban 4001", "031 201 6107", latitude=-29.8545, longitude=31.0121),
    Residence("francis-farel", "Francis Farel Residence", "Overport", "18 Francis Farel Road, Overport, Durban 4001", "031 207 6108", latitude=-29.8338, longitude=31.0109),
    Residence("glebelands", "Glebelands Residence", "Umlazi", "Glebelands Drive, Umlazi, Durban 4031", "031 906 6109", latitude=-29.9625, longitude=30.8832),
    Residence("glenmore", "Glenmore Residence", "Glenwood", "9 Glenmore Avenue, Glenwood, Durban 4001", "031 205 6110", latitude=-29.8744, longitude=31.0015),
    Residence("howard-college", "Howard College Residence", "Berea", "University Road, Berea, Durban 4001", "031 260 6111", latitude=-29.8670, longitude=30.9788),
    Residence("joseph-ndlela", "Joseph Ndlela Residence", "Steve Biko Campus", "79 Mansfield Road, Durban 4001", "031 204 6112", latitude=-29.8530, longitude=31.0020),
    Residence("king-edward", "King Edward Residence", "Berea", "18 King Edward Avenue, Berea, Durban 4001", "031 201 6113", latitude=-29.8612, longitude=31.0091),
    Residence("lower-campus", "Lower Campus Residence", "Steve Biko Campus", "Steve Biko Campus, Durban 4001", "031 204 6114", latitude=-29.8540, longitude=31.0031),
    Residence("manning-road", "Manning Road Residence", "Berea", "25 Manning Road, Berea, Durban 4001", "031 201 6115", latitude=-29.8615, longitude=31.0150),
    Residence("ml-sultan", "ML Sultan Residence", "Durban Central", "51 Sulty Lane, Durban 4001", "031 308 6116", latitude=-29.8492, longitude=31.0265),
    Residence("parkside", "Parkside Residence", "Berea", "7 Parkside Road, Berea, Durban 4001", "031 201 6117", latitude=-29.8560, longitude=31.0058),
    Residence("peter-maritz", "Peter Maritz Residence", "Berea", "14 Peter Maritz Road, Berea, Durban 4001", "031 201 6118", latitude=-29.8592, longitude=31.0069),
    Residence("premier-road", "Premier Road Residence", "Overport", "22 Premier Road, Overport, Durban 4001", "031 207 6119", latitude=-29.8385, longitude=31.0118),
    Residence("quarry-road", "Quarry Road Residence", "Overport", "4 Quarry Road, Overport, Durban 4001", "031 207 6120", latitude=-29.8317, longitude=31.0161),
    Residence("riverside", "Riverside Residence", "Durban Central", "3 Riverside Road, Durban 4001", "031 304 6121", latitude=-29.8518, longitude=31.0210),
    Residence("ritson-road", "Ritson Road Residence", "Durban Central", "33 Ritson Road, Durban 4001", "031 308 6122", latitude=-29.8510, longitude=31.0225),
    Residence("salisbury", "Salisbury Residence", "Berea", "10 Salisbury Road, Berea, Durban 4001", "031 201 6123", latitude=-29.8645, longitude=31.0130),
    Residence("sherwood", "Sherwood Residence", "Sherwood", "26 Sherwood Road, Sherwood, Durban 4091", "031 209 6124", latitude=-29.8588, longitude=30.9950),
    Residence("south-beach", "South Beach Residence", "Beachfront", "25 Marine Parade, Durban 4001", "031 337 6125", latitude=-29.8640, longitude=31.0396),
    Residence("st-james", "St James Residence", "Berea", "19 St James Avenue, Berea, Durban 4001", "031 201 6126", latitude=-29.8660, longitude=31.0100),
    Residence("umbilo", "Umbilo Residence", "Umbilo", "31 Umbilo Road, Durban 4013", "031 467 6127", latitude=-29.9145, longitude=30.9940),
    Residence("victoria-park", "Victoria Park Residence", "Berea", "8 Victoria Park, Berea, Durban 4001", "031 201 6128", latitude=-29.8600, longitude=31.0125),
    Residence("westville", "Westville Residence", "Westville", "University Estate, Westville, Durban 3629", "031 260 6129", latitude=-29.8170, longitude=30.9380),
)

_BY_KEY = {residence.key: residence for residence in DUT_RESIDENCES}


def get_residence(key: str | None) -> Residence | None:
    return _BY_KEY.get(key or "")


def residence_choices() -> list[tuple[str, str]]:
    """Flat ``(key, label)`` choices, for validation."""
    return [(r.key, r.name) for r in DUT_RESIDENCES] + [(OTHER_ACCOMMODATION, "Other / private accommodation")]


def grouped_residences() -> list[tuple[str, list[Residence]]]:
    """Residences grouped by area, areas alphabetical, for ``<optgroup>`` rendering."""
    groups: dict[str, list[Residence]] = {}
    for residence in sorted(DUT_RESIDENCES, key=lambda r: (r.area, r.name)):
        groups.setdefault(residence.area, []).append(residence)
    return list(groups.items())
