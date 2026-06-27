import requests
import urllib3
import json


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def test001():
    url = 'https://blrslgsidt00254:8643/tiplus2-zone1-api/issued-undertakings/issuances'
    headers = {'Authorization': 'Bearer '
                  'eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJMNXZUSGI4YTk4MzJ1QV9kQUVqYVRGLUhZRDN5elY3OXNYUkQ4M3VlX0w0In0.eyJleHAiOjE3ODI1Nzc0ODEsImlhdCI6MTc4MjU3NzE4MSwianRpIjoiODU1OTljZDAtNjUxYS00OWM0LWIyNGUtYTBmNTAyMDAyZWJhIiwiaXNzIjoiaHR0cDovLzEwLjIwNC4xMDYuMTQ1OjgwOTAvcmVhbG1zL0ZpbkNvbm5lY3QiLCJhdWQiOiJhY2NvdW50Iiwic3ViIjoiZjFhMGViYmYtODYzNi00MDdkLTkxOWMtOTk5MzkwN2VlNzEzIiwidHlwIjoiQmVhcmVyIiwiYXpwIjoiZmluY29ubmVjdCIsImFjciI6IjEiLCJhbGxvd2VkLW9yaWdpbnMiOlsiLyoiXSwicmVhbG1fYWNjZXNzIjp7InJvbGVzIjpbImRlZmF1bHQtcm9sZXMtZmluY29ubmVjdCIsIm9mZmxpbmVfYWNjZXNzIiwidW1hX2F1dGhvcml6YXRpb24iXX0sInJlc291cmNlX2FjY2VzcyI6eyJmaW5jb25uZWN0Ijp7InJvbGVzIjpbInVtYV9wcm90ZWN0aW9uIl19LCJhY2NvdW50Ijp7InJvbGVzIjpbIm1hbmFnZS1hY2NvdW50IiwibWFuYWdlLWFjY291bnQtbGlua3MiLCJ2aWV3LXByb2ZpbGUiXX19LCJzY29wZSI6ImVtYWlsIHByb2ZpbGUiLCJjbGllbnRIb3N0IjoiMTAuMTE5LjIxNy4zIiwiZW1haWxfdmVyaWZpZWQiOmZhbHNlLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJzZXJ2aWNlLWFjY291bnQtZmluY29ubmVjdCIsImNsaWVudEFkZHJlc3MiOiIxMC4xMTkuMjE3LjMiLCJjbGllbnRfaWQiOiJmaW5jb25uZWN0In0.O7Y4yvy5kGVwRwI4qQxSPVFgDap_dzELJ1Hv6aba0byY0GU_7KTXl-jbvN15gKifUTSeRCBAC6q6Hc-TFqFVpMwcwp_LTyJ_XxR7px8p8glJGHCZ2JxbGp4h3Vj0s6JcRP2rJZcczGRsBtAsj82TUAmIlrcI3QVEZUhyFbwHZ3bffA5-GRXAVKRQ9kkeXJHqolzGL-Re8X3yPP5FDrVFxpqeCGWmpvQPFqRvyM42GDrptmGXdSBIGHduLEY4FERXH2Ug5VM5ym58Sa7ecOkGCJZnkMIDKmggtPujpi4MsxIPrAsjd8dSez1M2IIo4WmjT08DNs8JvU-0wHi3tv4Kfg',
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
    response = requests.request(method='POST', url=url, headers=headers, json=payload, timeout=30, verify=False)

    try:
        data = response.json()
    except ValueError:
        data = response.text
    print(f'Status: {response.status_code}')
    print('Response:')
    print(data)
    if response.status_code != 201:
        raise AssertionError(f'Expected status 201, got {response.status_code}')


if __name__ == '__main__':
    test001()
