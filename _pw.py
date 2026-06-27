import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://att-lcs-dev1-fti.11c.qa.provides.io/tiplus2-global/login")
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("pat_inp")
    page.get_by_role("textbox", name="Username").press("Tab")
    page.get_by_role("textbox", name="Password").fill("passsword1")
    page.get_by_role("textbox", name="Password").press("Enter")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("pat_inp")
    page.get_by_role("textbox", name="Username").press("Tab")
    page.get_by_role("textbox", name="Password").fill("password1")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("cell", name="ZONE1", exact=True).click()
    page.get_by_role("button", name="Start...").click()
    page.get_by_text("Trade Finance Processing").click()
    page.get_by_role("button", name="Start...").click()
    page.goto("https://att-lcs-dev1-fti.11c.qa.provides.io/tiplus2-zone1/content/TIPlusWP.jsf")
    page.get_by_role("button", name="OK").click()
    page.get_by_role("button", name="Master browser").click()
    page.locator("[id=\"_id3:OpenTFProcessMasterWP_NewMasterWP_BehalfOfBranchWP_ctlBranchCode\"]").click()
    page.locator("[id=\"_id3:OpenTFProcessMasterWP_NewMasterWP_BehalfOfBranchWP_ctlBranchCode\"]").fill("LON")
    page.locator("[id=\"_id3:OpenTFProcessMasterWP_NewMasterWP_BehalfOfBranchWP_ctlBranchCode\"]").press("Tab")
    page.locator("[id=\"_id3:OpenTFProcessMasterWP_NewMasterWP_ctlSelectedMaster\"]").select_option("442]]443]]")
    page.goto("https://att-lcs-dev1-fti.11c.qa.provides.io/tiplus2-zone1/content/OpenTFProcessMasterWP.jsf")
    page.get_by_role("button", name="New...").click()
    page.locator("[id=\"_id3:CreateCleanBAEventWP_ctlargCreateCleanBAEvent_EventDetails_transfer_method\"]").select_option("i")
    page.locator("[id=\"_id3:CreateCleanBAEventWP_CreateCleanBADraftP_CleanBADraftP_TenorExtendedPeriodWP_FromAfterWP_ctlOptionList_CurrentOption\"]").select_option("A")
    page.locator("[id=\"_id3:CreateCleanBAEventWP_CreateCleanBADraftP_CleanBADraftP_TenorExtendedPeriodWP_ctlWorkTenorFrom\"]").select_option("A")
    page.goto("https://att-lcs-dev1-fti.11c.qa.provides.io/tiplus2-zone1/content/CreateCleanBAEventWP.jsf")
    page.get_by_text("Auto mature").click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
