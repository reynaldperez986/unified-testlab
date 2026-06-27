import csv
import json
import sys
from datetime import datetime
from pathlib import Path
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as exc:
    if exc.name == 'playwright':
        raise SystemExit('Playwright is not installed. Run: python -m pip install playwright && python -m playwright install chromium')
    raise


def _save_response_outputs(data):
    output_dir = Path.cwd() / 'Response'
    output_dir.mkdir(exist_ok=True)
    file_base = output_dir / 'Test001_response'
    json_path = file_base.with_suffix('.json')
    csv_path = file_base.with_suffix('.csv')

    with open(json_path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)

    with open(csv_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                fieldnames = sorted({key for item in data if isinstance(item, dict) for key in item.keys()})
                writer.writerow(fieldnames)
                for item in data:
                    writer.writerow([json.dumps(item.get(field, ''), ensure_ascii=False) if isinstance(item.get(field, ''), (dict, list)) else item.get(field, '') for field in fieldnames])
            else:
                writer.writerow(['Value'])
                for item in data:
                    writer.writerow([item])
        elif isinstance(data, dict):
            writer.writerow(['Key', 'Value'])
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                writer.writerow([key, value])
        else:
            writer.writerow(['Response'])
            writer.writerow([data])

    print(f'JSON saved to: {json_path}')
    print(f'CSV saved to: {csv_path}')


def test001():
    url = 'https://blrslgsidt00254:8643/tiplus2-zone1-api/issued-undertakings/issuances'
    headers = {'Authorization': 'Bearer '
                  'eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJMNXZUSGI4YTk4MzJ1QV9kQUVqYVRGLUhZRDN5elY3OXNYUkQ4M3VlX0w0In0.eyJleHAiOjE3ODI1Nzg1OTksImlhdCI6MTc4MjU3ODI5OSwianRpIjoiMTZkM2E4ZDktYjZmMC00ZmRhLTljZTEtNTUwY2E4M2FjNWU0IiwiaXNzIjoiaHR0cDovLzEwLjIwNC4xMDYuMTQ1OjgwOTAvcmVhbG1zL0ZpbkNvbm5lY3QiLCJhdWQiOiJhY2NvdW50Iiwic3ViIjoiZjFhMGViYmYtODYzNi00MDdkLTkxOWMtOTk5MzkwN2VlNzEzIiwidHlwIjoiQmVhcmVyIiwiYXpwIjoiZmluY29ubmVjdCIsImFjciI6IjEiLCJhbGxvd2VkLW9yaWdpbnMiOlsiLyoiXSwicmVhbG1fYWNjZXNzIjp7InJvbGVzIjpbImRlZmF1bHQtcm9sZXMtZmluY29ubmVjdCIsIm9mZmxpbmVfYWNjZXNzIiwidW1hX2F1dGhvcml6YXRpb24iXX0sInJlc291cmNlX2FjY2VzcyI6eyJmaW5jb25uZWN0Ijp7InJvbGVzIjpbInVtYV9wcm90ZWN0aW9uIl19LCJhY2NvdW50Ijp7InJvbGVzIjpbIm1hbmFnZS1hY2NvdW50IiwibWFuYWdlLWFjY291bnQtbGlua3MiLCJ2aWV3LXByb2ZpbGUiXX19LCJzY29wZSI6ImVtYWlsIHByb2ZpbGUiLCJjbGllbnRIb3N0IjoiMTAuMTE5LjIxNy4zIiwiZW1haWxfdmVyaWZpZWQiOmZhbHNlLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJzZXJ2aWNlLWFjY291bnQtZmluY29ubmVjdCIsImNsaWVudEFkZHJlc3MiOiIxMC4xMTkuMjE3LjMiLCJjbGllbnRfaWQiOiJmaW5jb25uZWN0In0.ga5bKeItV4A46gHnbRDr0WR1t2A2KS2soxSmHEqwziXtV0xHZ8fDyJxxHjy7XFWe-S-WqrbSiqhqqNCjl22fP86UvwBEu1uw7pfriwRRmVzneQBBCGMAoOdWYzXsRSYlUwECU4StUlH1gWUqLYgHC1S5Nz_zodhY7CuETQIHxKcP1t65CwKORRXgA-_ya1-mCPSTNOIlM2I6psjydSfXevaBBu2c47WiLftOuqs1mW6Wj1TTZeYGChnw3UjqvBC-WgyFe4kwomM0dvuOzBLSruCDIEh6R0JY6VG-HxRkVur8geNlJFGD1yxWuyc9bzpHT1YtIHVWYYy5YGxtECQUFg',
 'Content-Type': 'application/json',
 'Idempotency-Key': '{{$randomUUID}}'}
    payload = json.loads(r'''{
  "inputBranch": "NYNY",
  "behalfOfBranch": "NYNY",
  "actionItems": [
    {
      "messageData": "Finance details have been arranged for this transaction. Contact the Finance department with reference 09020019-001 for further information",
      "messageDescription": "Input action required",
      "messageInfo": "Document cross-check done by J.D.",
      "messageNumber": "00001",
      "actioned": true
    }
  ],
  "requestType": "ISSUE-COUNTER-REQUEST-COUNTER",
  "senderReference": "ABC001",
  "instructionsReceived": {
    "instructingParty": "COUNTER-RECEIVED-FROM-BANK",
    "issueBy": "EMAIL",
    "applicationDate": "2011-05-18"
  },
  "partyInstructions": {
    "instructionsFromInstructingParty": "instructionsFromInstructingParty",
    "responseToInstructingParty": "responseToInstructingParty",
    "instructionsToNextParty": "instructionsToNextParty"
  },
  "adviseDirect": false,
  "applicant": {
    "customerId": "ABC",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "ABC INDUSTRIES LTD.",
      "addressLine1": "SLOUGH ESTATES",
      "addressLine2": "BATH ROAD",
      "addressLine3": "SLOUGH",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "ABC001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "email@mail.com"
  },
  "beneficiary": {
    "customerId": "AMALGA",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "Amalgamated Mouldings Limited",
      "addressLine1": "45 Bath Road",
      "addressLine2": "READING",
      "addressLine3": "Berkshire",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "ALKHBH21001",
    "reference": "AMA001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "ama@limited.com"
  },
  "beneficiaryAccountNumber": "string",
  "issuingBank": {
    "customerId": "ALKH",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "Al-Khalifa Bank",
      "addressLine1": "112 Doha Road",
      "addressLine2": "Bahrain",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "ALKHALIFA001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "alk@bank.com"
  },
  "advisingBank": {
    "customerId": "BOST",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "THE BANK OF BOSTON",
      "addressLine1": "150 ROYAL STREET",
      "addressLine2": "BOSTON, MA",
      "addressLine3": "string",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "BOST001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "bost@bank.com"
  },
  "adviseThruBank": {
    "customerId": "BOST",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "THE BANK OF BOSTON",
      "addressLine1": "150 ROYAL STREET",
      "addressLine2": "BOSTON, MA",
      "addressLine3": "string",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "BOST001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "abci@ndustries.com"
  },
  "counterReceivedFromBank": {
    "customerId": "ABC",
    "nameAndAddress": {
      "name": "ABC INDUSTRIES LTD.",
      "addressLine1": "SLOUGH ESTATES",
      "addressLine2": "BATH ROAD",
      "addressLine3": "SLOUGH",
      "iso20022NameAndAddress": {
        "name": "string",
        "department": "string",
        "subDepartment": "string",
        "streetName": "string",
        "buildingNumber": "string",
        "buildingName": "string",
        "floor": "string",
        "postBox": "string",
        "room": "string",
        "postCode": "string",
        "townName": "string",
        "townLocationName": "string",
        "districtName": "string",
        "countrySubDivision": "string",
        "country": "GB"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "ABC001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "abci@ndustries.com"
  },
  "principalNotApplicant": {
    "customerId": "ABC",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "ABC INDUSTRIES LTD.",
      "addressLine1": "SLOUGH ESTATES",
      "addressLine2": "BATH ROAD",
      "addressLine3": "SLOUGH",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "ABC001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "emai@mail.com"
  },
  "undertaking": {
    "formOfUndertaking": "STANDBY-LETTER-OF-CREDIT",
    "productType": "ADV",
    "applicableRulesUndertaking": {
      "code": "NONE",
      "narrative": "string"
    },
    "issueDate": "2011-05-18",
    "expiry": {
      "expiryType": "CONDITIONAL",
      "date": "2011-05-22",
      "condition": "string",
      "domesticExpiry": false,
      "automaticRelease": false
    },
    "drawingDetails": {
      "notPayableBefore": "2011-05-19",
      "partialDrawings": false,
      "multipleDrawings": false
    },
    "governingLaw": {
      "governingLawCountry": "GB",
      "placeOfJurisdiction": "Place of jurisdiction",
      "countrySubDivision": "string"
    },
    "undertakingAmount": {
      "amount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "amountSpecification": {
        "amountMax": "100000.00",
        "amountMin": "100000.00",
        "qualifier": "OTHER",
        "min": 5,
        "max": 10
      }
    },
    "additionalAmountsCoveredUndertaking": {
      "additionalAmount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "additionalAmountsdescription": "Additional amounts covered undertaking"
    },
    "availableWith": {
      "availableWithType": "NAMED-BANK",
      "party": {
        "customerId": "ABC",
        "nameAndAddress": {
          "name": "ABC INDUSTRIES LTD.",
          "addressLine1": "SLOUGH ESTATES",
          "addressLine2": "BATH ROAD",
          "addressLine3": "SLOUGH",
          "iso20022NameAndAddress": {
            "name": "string",
            "department": "string",
            "subDepartment": "string",
            "streetName": "string",
            "buildingNumber": "string",
            "buildingName": "string",
            "floor": "string",
            "postBox": "string",
            "room": "string",
            "postCode": "string",
            "townName": "string",
            "townLocationName": "string",
            "districtName": "string",
            "countrySubDivision": "string",
            "country": "GB"
          }
        },
        "country": "GB",
        "postalCode": "string",
        "swiftAddress": "string",
        "reference": "ABC001",
        "contactName": "John Smith",
        "contactNumbers": [
          {
            "type": "TELEPHONE",
            "number": "+44 (1753) 890 171"
          }
        ],
        "telexDetails": {
          "number": "871161147",
          "answerBack": "57254738"
        },
        "email": "abci@ndustries.com"
      },
      "country": "GB",
      "city": "London City"
    },
    "documentAndPresentationInstructions": "Document and presentation instructions",
    "termsAndConditions": "Terms and condition of issued undertaking",
    "renewalDetails": {
      "renewal": {
        "renewalAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "calendarDate": "2011-05-19",
        "isRollingRenewal": true,
        "nextExpiryDate": "2012-05-19",
        "nonExtensionNoticationDetails": "Non-extension notification details.",
        "renewForPeriod": {
          "periodNumber": 2,
          "periodUnit": "DAYS"
        },
        "renewalDate": "2011-05-19",
        "renewalOn": true,
        "renewalWhen": "ON-CALENDAR",
        "rollingRenewal": {
          "cancellationNotice": 0,
          "everyPeriod": {
            "periodNumber": 1,
            "periodUnit": "DAYS",
            "daysInMonth": 0
          },
          "numberOf": 1,
          "renewOn": "EVERY",
          "adjustedFinalExpiryDate": "2012-05-19"
        },
        "useAmount": "CURRENT"
      },
      "reduction": {
        "reductionAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "amountOrPercent": {
          "amount": "100000.00",
          "percent": 99.75
        },
        "isIncrease": true,
        "isRegular": true,
        "maxIncrease": 0,
        "period": {
          "periodNumber": 1,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "reductionDate": "2011-05-19",
        "remainingNo": 0,
        "startDate": "2011-05-19",
        "irregularAmounts": [
          {
            "amount": "100000.00",
            "date": "2011-05-19",
            "isIncrease": true,
            "percent": 99.75
          }
        ]
      },
      "regularRenewal": {
        "adviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "everyPeriod": {
          "periodNumber": 1,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "extensionDetails": "string",
        "nonExtensionNoticationDetails": "string",
        "notificationDays": 0,
        "numberOfDays": 3,
        "numberOfRenewals": 2,
        "renewFor": "CALENDAR-DAYS",
        "useAmount": "CURRENT"
      }
    }
  },
  "counterReceived": {
    "formOfUndertaking": "DEMAND-GUARANTEE",
    "productType": "ADE",
    "applicableRulesUndertaking": {
      "code": "NONE",
      "narrative": "string"
    },
    "issueDate": "2011-05-18",
    "expiry": {
      "expiryType": "CONDITIONAL",
      "date": "2011-06-11",
      "approximateExpiryDate": "2012-06-11",
      "condition": "string",
      "domesticExpiry": false,
      "automaticRelease": false
    },
    "drawingDetails": {
      "notPayableBefore": "2011-05-21",
      "partialDrawings": false,
      "multipleDrawings": false
    },
    "governingLaw": {
      "governingLawCountry": "GB",
      "placeOfJurisdiction": "Place of jurisdiction",
      "countrySubDivision": "string"
    },
    "undertakingAmount": {
      "amount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "amountSpecification": {
        "amountMax": "100000.00",
        "amountMin": "100000.00",
        "qualifier": "OTHER",
        "min": 0,
        "max": 0
      }
    },
    "additionalAmountsCoveredUndertaking": {
      "additionalAmount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "additionalAmountsdescription": "Additional amounts covered undertaking"
    },
    "availableWith": {
      "availableWithType": "NAMED-BANK",
      "party": {
        "customerId": "ABC",
        "nameAndAddress": {
          "name": "ABC INDUSTRIES LTD.",
          "addressLine1": "SLOUGH ESTATES",
          "addressLine2": "BATH ROAD",
          "addressLine3": "SLOUGH",
          "iso20022NameAndAddress": {
            "name": "string",
            "department": "string",
            "subDepartment": "string",
            "streetName": "string",
            "buildingNumber": "string",
            "buildingName": "string",
            "floor": "string",
            "postBox": "string",
            "room": "string",
            "postCode": "string",
            "townName": "string",
            "townLocationName": "string",
            "districtName": "string",
            "countrySubDivision": "string",
            "country": "GB"
          }
        },
        "country": "GB",
        "postalCode": "string",
        "swiftAddress": "string",
        "reference": "ABC001",
        "contactName": "John Smith",
        "contactNumbers": [
          {
            "type": "TELEPHONE",
            "number": "+44 (1753) 890 171"
          }
        ],
        "telexDetails": {
          "number": "871161147",
          "answerBack": "57254738"
        },
        "email": "abci@ndustries.com"
      },
      "country": "GB",
      "city": "London City"
    },
    "documentAndPresentationInstructions": "Document and presentation instructions",
    "termsAndConditions": "Terms and condition of issued undertaking",
    "renewalDetails": {
      "renewal": {
        "renewalAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "calendarDate": "2011-05-18",
        "isRollingRenewal": true,
        "nextExpiryDate": "2012-06-11",
        "nonExtensionNoticationDetails": "Non-extension notification details.",
        "renewForPeriod": {
          "periodNumber": 1,
          "periodUnit": "DAYS"
        },
        "renewalDate": "2012-05-21",
        "renewalOn": true,
        "renewalWhen": "ON-CALENDAR",
        "rollingRenewal": {
          "cancellationNotice": 0,
          "everyPeriod": {
            "periodNumber": 1,
            "periodUnit": "DAYS",
            "daysInMonth": 0
          },
          "numberOf": 1,
          "renewOn": "EVERY",
          "adjustedFinalExpiryDate": "2011-06-11"
        },
        "useAmount": "CURRENT"
      },
      "reduction": {
        "reductionAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "amountOrPercent": {
          "amount": "100000.00",
          "percent": 99.75
        },
        "isIncrease": true,
        "isRegular": true,
        "maxIncrease": 0,
        "period": {
          "periodNumber": 0,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "reductionDate": "2012-05-21",
        "remainingNo": 0,
        "startDate": "2019-05-11",
        "irregularAmounts": [
          {
            "amount": "100000.00",
            "date": "2012-05-11",
            "isIncrease": true,
            "percent": 99.75
          }
        ]
      },
      "regularRenewal": {
        "adviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "everyPeriod": {
          "periodNumber": 0,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "extensionDetails": "string",
        "nonExtensionNoticationDetails": "string",
        "notificationDays": 0,
        "numberOfDays": 3,
        "numberOfRenewals": 2,
        "renewFor": "CALENDAR-DAYS",
        "useAmount": "CURRENT"
      }
    }
  },
  "counterToSend": {
    "formOfUndertaking": "STANDBY-LETTER-OF-CREDIT",
    "productType": "ADV",
    "applicableRulesUndertaking": {
      "code": "NONE",
      "narrative": "string"
    },
    "issueDate": "2011-05-18",
    "expiry": {
      "date": "2012-06-11",
      "approximateExpiryDate": "2012-06-11",
      "condition": "string",
      "domesticExpiry": false,
      "automaticRelease": false
    },
    "drawingDetails": {
      "notPayableBefore": "2012-05-21",
      "partialDrawings": false,
      "multipleDrawings": false
    },
    "governingLaw": {
      "governingLawCountry": "GB",
      "placeOfJurisdiction": "Place of jurisdiction",
      "countrySubDivision": "string"
    },
    "undertakingAmount": {
      "amount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "amountSpecification": {
        "amountMax": "100000.00",
        "amountMin": "100000.00",
        "qualifier": "OTHER",
        "min": 0,
        "max": 0
      }
    },
    "additionalAmountsCoveredUndertaking": {
      "additionalAmount": {
        "amount": "100000.00",
        "currency": "USD"
      },
      "additionalAmountsdescription": "Additional amounts covered undertaking"
    },
    "availableWith": {
      "availableWithType": "NAMED-BANK",
      "party": {
        "customerId": "ABC",
        "nameAndAddress": {
          "name": "ABC INDUSTRIES LTD.",
          "addressLine1": "SLOUGH ESTATES",
          "addressLine2": "BATH ROAD",
          "addressLine3": "SLOUGH",
          "iso20022NameAndAddress": {
            "name": "Name",
            "department": "Department",
            "subDepartment": "Sub department",
            "streetName": "Street name",
            "buildingNumber": "1",
            "buildingName": "Building Name",
            "floor": "3rd",
            "postBox": "1234",
            "room": "301",
            "postCode": "1234",
            "townName": "Town A",
            "townLocationName": "Town Loc",
            "districtName": "District Name",
            "countrySubDivision": "Country Subd",
            "country": "ST"
          }
        },
        "country": "GB",
        "postalCode": "string",
        "swiftAddress": "DBOFUS31XXX",
        "reference": "DAIWA001",
        "contactName": "John Smith",
        "contactNumbers": [
          {
            "type": "TELEPHONE",
            "number": "+44 (1753) 890 171"
          }
        ],
        "telexDetails": {
          "number": "871161147",
          "answerBack": "57254738"
        },
        "email": "daiwa@bankny.com"
      },
      "country": "GB",
      "city": "London City"
    },
    "documentAndPresentationInstructions": "Document and presentation instructions",
    "termsAndConditions": "Terms and condition of issued undertaking",
    "renewalDetails": {
      "renewal": {
        "renewalAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "calendarDate": "2011-05-20",
        "isRollingRenewal": true,
        "nextExpiryDate": "2012-05-22",
        "nonExtensionNoticationDetails": "Non-extension notification details.",
        "renewForPeriod": {
          "periodNumber": 1,
          "periodUnit": "DAYS"
        },
        "renewalDate": "2011-05-20",
        "renewalOn": true,
        "renewalWhen": "ON-CALENDAR",
        "rollingRenewal": {
          "cancellationNotice": 0,
          "everyPeriod": {
            "periodNumber": 2,
            "periodUnit": "DAYS",
            "daysInMonth": 1
          },
          "numberOf": 1,
          "renewOn": "EVERY",
          "adjustedFinalExpiryDate": "2011-05-23"
        },
        "useAmount": "CURRENT"
      },
      "reduction": {
        "reductionAdviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "amountOrPercent": {
          "amount": "100000.00",
          "percent": 99.75
        },
        "isIncrease": true,
        "isRegular": true,
        "maxIncrease": 0,
        "period": {
          "periodNumber": 0,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "reductionDate": "2011-05-20",
        "remainingNo": 0,
        "startDate": "2011-05-18",
        "irregularAmounts": [
          {
            "amount": "100000.00",
            "date": "2011-05-18",
            "isIncrease": true,
            "percent": 99.75
          }
        ]
      },
      "regularRenewal": {
        "adviseDetails": {
          "advise": true,
          "adviseNoticeDays": 0
        },
        "everyPeriod": {
          "periodNumber": 0,
          "periodUnit": "DAYS",
          "daysInMonth": 0
        },
        "extensionDetails": "string",
        "nonExtensionNoticationDetails": "string",
        "notificationDays": 0,
        "numberOfDays": 3,
        "numberOfRenewals": 2,
        "renewFor": "CALENDAR-DAYS",
        "useAmount": "CURRENT"
      }
    }
  },
  "typeOfLocalUndertaking": {
    "typeOfLocalUndertakingCode": "ADVANCE-PAYMENT",
    "additionalText": "string"
  },
  "adviseThruBankUndertaking": {
    "customerId": "DEUTSCHE",
    "legalEntityIdentifier": "9845003A5A79CBF12C88",
    "nameAndAddress": {
      "name": "Deutsche Bank",
      "addressLine1": "Taunusanlage 12",
      "addressLine2": "60325 FRANKFURT AM MAIN ",
      "addressLine3": "GERMANY",
      "iso20022NameAndAddress": {
        "name": "Name",
        "department": "Department",
        "subDepartment": "Sub department",
        "streetName": "Street name",
        "buildingNumber": "1",
        "buildingName": "Building Name",
        "floor": "3rd",
        "postBox": "1234",
        "room": "301",
        "postCode": "1234",
        "townName": "Town A",
        "townLocationName": "Town Loc",
        "districtName": "District Name",
        "countrySubDivision": "Country Subd",
        "country": "ST"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "DEUTDEFFXXX",
    "reference": "ABC001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "deutsche@bank.com"
  },
  "adviseThruBankUndertakingAccount": "364381039",
  "adviseThruBankAccount": "364381039",
  "contractDetails": {
    "referenceCode": "CONTRACT",
    "referenceNarrative": "Narrative for the contract details",
    "referenceDate": "2011-05-20",
    "tenderClosingDate": "2011-05-20",
    "totalOrderAmount": {
      "amount": "100000.00",
      "currency": "USD"
    },
    "guaranteeValuePercent": 99.75
  },
  "finalIssuingBank": {
    "customerId": "ABC",
    "nameAndAddress": {
      "name": "ABC INDUSTRIES LTD.",
      "addressLine1": "SLOUGH ESTATES",
      "addressLine2": "BATH ROAD",
      "addressLine3": "SLOUGH",
      "iso20022NameAndAddress": {
        "name": "ABC",
        "department": "department",
        "subDepartment": "subDepartment",
        "streetName": "string",
        "buildingNumber": "string",
        "buildingName": "string",
        "floor": "string",
        "postBox": "string",
        "room": "string",
        "postCode": "string",
        "townName": "string",
        "townLocationName": "string",
        "districtName": "string",
        "countrySubDivision": "string",
        "country": "GB"
      }
    },
    "country": "GB",
    "postalCode": "string",
    "swiftAddress": "string",
    "reference": "ABC001",
    "contactName": "John Smith",
    "contactNumbers": [
      {
        "type": "TELEPHONE",
        "number": "+44 (1753) 890 171"
      }
    ],
    "telexDetails": {
      "number": "871161147",
      "answerBack": "57254738"
    },
    "email": "abci@ndustries.com"
  },
  "financialTrade": "TRADE",
  "underTakingTermsAndCondition": {
    "wordingType": "APPLICANT-WORDING",
    "requestStandardWording": true,
    "requestLanguage": "ab",
    "wordingLanguage": "es"
  },
  "underlyingTransactionDetails": "string",
  "transferDetails": {
    "transferable": true,
    "transferableConditions": "string"
  },
  "deliveryDetails": {
    "deliveryByMethod": "COLLECTION",
    "additionalInformation": "string",
    "deliveryToOrCollectionBy": "OTHER-ADDRESSEE",
    "deliveryToParty": {
      "customerId": "ABC",
      "legalEntityIdentifier": "9845003A5A79CBF12C88",
      "nameAndAddress": {
        "name": "ABC INDUSTRIES LTD.",
        "addressLine1": "SLOUGH ESTATES",
        "addressLine2": "BATH ROAD",
        "addressLine3": "SLOUGH",
        "iso20022NameAndAddress": {
          "name": "Name",
          "department": "Department",
          "subDepartment": "Sub department",
          "streetName": "Street name",
          "buildingNumber": "1",
          "buildingName": "Building Name",
          "floor": "3rd",
          "postBox": "1234",
          "room": "301",
          "postCode": "1234",
          "townName": "Town A",
          "townLocationName": "Town Loc",
          "districtName": "District Name",
          "countrySubDivision": "Country Subd",
          "country": "ST"
        }
      },
      "country": "GB",
      "postalCode": "string",
      "swiftAddress": "string",
      "reference": "ABC001",
      "contactName": "John Smith",
      "contactNumbers": [
        {
          "type": "TELEPHONE",
          "number": "+44 (1753) 890 171"
        }
      ],
      "telexDetails": {
        "number": "871161147",
        "answerBack": "57254738"
      },
      "email": "abci@ndustries.com"
    }
  },
  "chargeDetails": {
    "ourCharges": "PRINCIPAL",
    "overseasChargesPayableBy": "PRINCIPAL",
    "deferCharges": true,
    "chargeAccountNumber": "9998-012843-100",
    "taxPayableBy": "CHARGE-PAYER",
    "preferredCurrency": "USD",
    "billingInvoiceAutomated": true,
    "userChargesText": "string"
  },
  "confirmationDetail": {
    "instruction": "UNCONFIRMED"
  },
  "shipmentDetails": {
    "from": "string",
    "to": "string",
    "portOfLoading": "string",
    "portOfDischarge": "string",
    "period": "2 weeks before expiry of LC.",
    "transhipment": "ALLOWED",
    "partialShipment": "ALLOWED",
    "incoTerms": "CFR",
    "incoPlace": "London",
    "insuranceForBuyer": false,
    "freightPayment": "COLLECT",
    "presentationDays": 10,
    "presentationPeriodNarrative": {
      "line1": "string",
      "line2": "string",
      "line3": "string",
      "line4": "string"
    },
    "documentsToBeSentBy": "AIR-MAIL",
    "numberOfDeliveryItems": 1,
    "billNumber": "string",
    "vesselName": "string",
    "shippingCompany": "string",
    "flightDetails": "string",
    "letterOfIndemnity": true
  },
  "goods": {
    "guaranteeAdditionalInformation": "string",
    "goodsCode": "ADMIN",
    "goodsDescription": "string"
  },
  "receivedRequestType": "COUNTER-RECEIVED-REQUEST-COUNTER",
  "additionalParties": [
    {
      "role": "NEW",
      "party": {
        "customerId": "ABC",
        "legalEntityIdentifier": "9845003A5A79CBF12C88",
        "nameAndAddress": {
          "name": "ABC INDUSTRIES LTD.",
          "addressLine1": "SLOUGH ESTATES",
          "addressLine2": "BATH ROAD",
          "addressLine3": "SLOUGH",
          "iso20022NameAndAddress": {
            "name": "name",
            "department": "department",
            "subDepartment": "subdepartment",
            "streetName": "street",
            "buildingNumber": "3",
            "buildingName": "building name",
            "floor": "1",
            "postBox": "123",
            "room": "456",
            "postCode": "1242",
            "townName": "town name",
            "townLocationName": "town location name",
            "districtName": "districtname",
            "countrySubDivision": "country sub division",
            "country": "US"
          }
        },
        "country": "GB",
        "postalCode": "string",
        "swiftAddress": "string",
        "reference": "ABC015",
        "contactName": "John Smith",
        "contactNumbers": [
          {
            "type": "TELEPHONE",
            "number": "+44 (1753) 890 171"
          }
        ],
        "telexDetails": {
          "number": "871161147",
          "answerBack": "57254738"
        },
        "email": "abc@industries.com"
      }
    }
  ]
}''')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        browser_context = browser.new_context(ignore_https_errors=True)
        page = browser_context.new_page()
        request_context = p.request.new_context(extra_http_headers=headers, ignore_https_errors=True)
        response = request_context.fetch(url, method='POST', data=json.dumps(payload))

        try:
            data = response.json()
        except Exception:
            data = response.text()
        print(f'Status: {response.status}')
        print('Response:')
        print(data)
        _save_response_outputs(data)
        if response.status != 201:
            raise AssertionError(f'Expected status 201, got {response.status}')

        page.goto('https://blrslgsidt00254:8643')
        # Add UI assertions or navigation steps here if this API call prepares UI state.

        request_context.dispose()
        browser_context.close()
        browser.close()


if __name__ == '__main__':
    test001()
