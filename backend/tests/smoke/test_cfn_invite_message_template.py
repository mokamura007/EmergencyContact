"""CFn smoke test — CognitoUserPool.AdminCreateUserConfig.InviteMessageTemplate.

Validates:
    - Requirement 2.1 (revised) admin registration from SPA: the Cognito
      invitation email uses a Japanese template so users receive an
      understandable password-setup mail rather than the AWS default
      English message.
    - Cognito API contract: ``EmailMessage`` must contain the ``{####}``
      placeholder (temporary password) or ``admin_create_user`` fails
      with ``InvalidParameterException``. ``{username}`` is optional at
      the API level but expected here for content completeness.

Fields under test:
    Resources.CognitoUserPool.Properties.AdminCreateUserConfig
      .InviteMessageTemplate.EmailSubject
      .EmailMessage

Behaviour under test (static template introspection only — no live
AWS interaction):
    1. The ``InviteMessageTemplate`` block exists under
       ``AdminCreateUserConfig`` (regression guard: this template was
       missing pre-Phase and adding it is the whole point of this task).
    2. ``EmailSubject`` is present and non-empty.
    3. ``EmailMessage`` is present, non-empty, contains ``{####}`` and
       ``{username}`` placeholders.
    4. ``EmailMessage`` includes the CloudFront / custom-domain URL
       injection via ``Fn::Sub`` with the ``LoginUrl`` mapping (proves
       that the environment-conditional URL is wired, not a hard-coded
       string).
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(scope="module")
def user_pool(cfn_resources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The CognitoUserPool resource block."""
    resource = cfn_resources.get("CognitoUserPool")
    assert isinstance(resource, dict), "CognitoUserPool resource missing"
    assert resource.get("Type") == "AWS::Cognito::UserPool"
    return resource


@pytest.fixture(scope="module")
def invite_message_template(user_pool: dict[str, Any]) -> dict[str, Any]:
    """The AdminCreateUserConfig.InviteMessageTemplate sub-block."""
    props = user_pool.get("Properties")
    assert isinstance(props, dict), "CognitoUserPool.Properties malformed"
    admin_cfg = props.get("AdminCreateUserConfig")
    assert isinstance(admin_cfg, dict), (
        "AdminCreateUserConfig missing on CognitoUserPool"
    )
    assert admin_cfg.get("AllowAdminCreateUserOnly") is True, (
        "AllowAdminCreateUserOnly must remain true (Requirement 1.9)"
    )
    imt = admin_cfg.get("InviteMessageTemplate")
    assert isinstance(imt, dict), (
        "InviteMessageTemplate missing under AdminCreateUserConfig — "
        "the Japanese admin invitation mail template must be defined."
    )
    return imt


def test_invite_message_template_has_email_subject(
    invite_message_template: dict[str, Any],
) -> None:
    """EmailSubject is present and non-empty."""
    subject = invite_message_template.get("EmailSubject")
    assert isinstance(subject, str) and subject.strip() != "", (
        "InviteMessageTemplate.EmailSubject must be a non-empty string"
    )
    # Sanity check: subject should reference the system name so users can
    # recognise it in an inbox. Loose match (any non-empty substring hint).
    assert "安否確認システム" in subject, (
        f"EmailSubject should identify the system: got {subject!r}"
    )


def test_invite_message_template_email_message_contains_placeholders(
    invite_message_template: dict[str, Any],
) -> None:
    """EmailMessage must contain ``{####}`` (mandatory) and ``{username}``.

    The Fn::Sub form serialises to a two-element list: [template, mapping].
    The first element is the template string that Cognito receives after
    CFn substitution of ``${LoginUrl}``; Cognito's own placeholders
    (``{####}`` / ``{username}``) survive Fn::Sub because ``!Sub`` only
    expands ``${...}`` patterns.
    """
    email_message = invite_message_template.get("EmailMessage")
    # Support both plain-string form and Fn::Sub form.
    template_str = _extract_template_string(email_message)

    assert "{####}" in template_str, (
        "Cognito requires the ``{####}`` placeholder in EmailMessage "
        "(temporary password insertion). Without it, admin_create_user "
        "fails with InvalidParameterException."
    )
    assert "{username}" in template_str, (
        "EmailMessage should include ``{username}`` so recipients know "
        "which login ID the temporary password applies to."
    )


def test_invite_message_template_uses_login_url_substitution(
    invite_message_template: dict[str, Any],
) -> None:
    """EmailMessage must be wired via Fn::Sub with a ``LoginUrl`` mapping.

    A plain hard-coded URL string would defeat the per-environment
    (custom domain vs *.cloudfront.net) URL injection contract.
    """
    email_message = invite_message_template.get("EmailMessage")
    assert isinstance(email_message, dict), (
        "EmailMessage must be an Fn::Sub form (dict), not a plain string"
    )
    assert "Fn::Sub" in email_message, (
        "EmailMessage must use Fn::Sub to inject the environment-"
        "conditional login URL"
    )
    sub_body = email_message["Fn::Sub"]
    assert isinstance(sub_body, list) and len(sub_body) == 2, (
        "EmailMessage Fn::Sub must be the [template, mapping] two-element "
        f"list form; got: {sub_body!r}"
    )
    template_str, mapping = sub_body
    assert isinstance(template_str, str)
    assert isinstance(mapping, dict) and "LoginUrl" in mapping, (
        "Fn::Sub mapping must define ``LoginUrl`` so the template can "
        f"reference ``${{LoginUrl}}``; got mapping: {mapping!r}"
    )
    assert "${LoginUrl}" in template_str, (
        "EmailMessage template must reference the ``${LoginUrl}`` "
        "substitution introduced by the Fn::Sub mapping"
    )


def test_invite_message_template_login_url_switches_on_custom_domain(
    invite_message_template: dict[str, Any],
) -> None:
    """The LoginUrl expression must branch on the HasCustomDomain Condition.

    Contract:
      * HasCustomDomain=true  -> ``https://${DomainName}/``
      * HasCustomDomain=false -> ``https://${SpaDistribution.DomainName}/``

    Introspecting the dict structure so that a future change replacing
    ``!If`` with a plain string (hard-coding one environment's URL) would
    fail the test.
    """
    email_message = invite_message_template.get("EmailMessage")
    assert isinstance(email_message, dict)
    _, mapping = email_message["Fn::Sub"]
    login_url = mapping["LoginUrl"]
    assert isinstance(login_url, dict) and "Fn::If" in login_url, (
        "LoginUrl must be an Fn::If expression keyed on HasCustomDomain; "
        f"got: {login_url!r}"
    )
    fn_if = login_url["Fn::If"]
    assert isinstance(fn_if, list) and len(fn_if) == 3
    condition_name, true_branch, false_branch = fn_if
    assert condition_name == "HasCustomDomain", (
        f"LoginUrl must branch on ``HasCustomDomain`` Condition, got: "
        f"{condition_name!r}"
    )
    # True branch: !Sub "https://${DomainName}/"
    assert _extract_sub_string(true_branch) == "https://${DomainName}/", (
        f"HasCustomDomain=true branch must yield ``https://${{DomainName}}/``, "
        f"got: {true_branch!r}"
    )
    # False branch: !Sub "https://${SpaDistribution.DomainName}/"
    assert (
        _extract_sub_string(false_branch)
        == "https://${SpaDistribution.DomainName}/"
    ), (
        f"HasCustomDomain=false branch must yield "
        f"``https://${{SpaDistribution.DomainName}}/``, got: {false_branch!r}"
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _extract_template_string(email_message: Any) -> str:
    """Return the EmailMessage template string regardless of Fn::Sub wrapping."""
    if isinstance(email_message, str):
        return email_message
    if isinstance(email_message, dict) and "Fn::Sub" in email_message:
        sub_body = email_message["Fn::Sub"]
        if isinstance(sub_body, list) and sub_body:
            first = sub_body[0]
            assert isinstance(first, str), (
                f"Fn::Sub template must be a string; got: {first!r}"
            )
            return first
        if isinstance(sub_body, str):
            return sub_body
    raise AssertionError(
        f"Unsupported EmailMessage shape: {email_message!r}"
    )


def _extract_sub_string(node: Any) -> str:
    """Return the raw template string from a ``Fn::Sub`` dict or plain string."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict) and "Fn::Sub" in node:
        body = node["Fn::Sub"]
        if isinstance(body, str):
            return body
        if isinstance(body, list) and body:
            first = body[0]
            assert isinstance(first, str)
            return first
    raise AssertionError(f"Unsupported Fn::Sub node shape: {node!r}")
