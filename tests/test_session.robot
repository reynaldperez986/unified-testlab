Here is the complete Robot Framework test suite generated from the provided JSON step list:

```robot
# Suite Setup
Suite Setup
    :args=${BASE_URL}
    Go To ${BASE_URL}

# Test Case 1
Test Case 1
    :args=${BASE_URL}
    *Documentation* Test Case 1
    *Suite Setup*

    :Variables*
        ${BASE_URL} = ${URL}

    Open Browser ${BASE_URL} browser=chrome

    :Keywords*
        Click Element ${userName} ${button}
        Double Click Element ${userName} ${button}
        Input Text ${userName} ${input}
        Select From List By Value / Input Text ${userName} ${input}
        Press Keys ${userName} ${key}
        Submit Form ${button}
        # Contextmenu (not supported by SeleniumLibrary, skipping)
        Navigate To ${url}

    :Test Steps*
        Click Element ${userName} ${button}
        Input Text ${userName} ${input}
        Click Element ${submit}
        Click Element ${radioButton}
        Change Input Text ${userName} ${input}
        Press Keys ${userName} ${key}
        Navigate To ${url}

    Close All Browsers

# Suite Teardown
Suite Teardown
    Close All Browsers
```

Note: The `*:args` syntax is used to pass arguments to the suite setup and teardown methods. In this case, we're passing the base URL as an argument. The `*:Variables*` and `*:Test Steps*` sections are used to define the variables and test steps, respectively. The `*:Keywords*` section is used to define the custom keywords (in this case, the actions performed by the test).