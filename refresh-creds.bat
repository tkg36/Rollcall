@echo off
REM Refresh AWS SSO credentials for 844905860028_AWSAdministratorAccess
REM Run this when you get "ExpiredTokenException" errors

echo Refreshing AWS SSO credentials...
aws sso login --profile 844905860028_AWSAdministratorAccess

echo.
echo Verifying credentials...
aws sts get-caller-identity --profile 844905860028_AWSAdministratorAccess

echo.
echo Done. Credentials refreshed.
pause
