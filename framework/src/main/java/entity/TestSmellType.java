package entity;

import lombok.AllArgsConstructor;
import lombok.Getter;

@Getter
@AllArgsConstructor
public enum TestSmellType {
    Assertion_Roulette("Assertion Roulette"),
    Conditional_Test_Logic("Conditional Test Logic"),
    Constructor_Initialization("Constructor Initialization"),
    Default_Test("Default Test"),
    EmptyTest("EmptyTest"),
    Exception_Catching_Throwing("Exception Catching Throwing"),
    General_Fixture("General Fixture"),
    Mystery_Guest("Mystery Guest"),
    Print_Statement("Print Statement"),
    Redundant_Assertion("Redundant Assertion"),
    Sensitive_Equality("Sensitive Equality"),
    Verbose_Test("Verbose Test"),
    Sleepy_Test("Sleepy Test"),
    Eager_Test("Eager Test"),
    Lazy_Test("Lazy Test"),
    Duplicate_Assert("Duplicate Assert"),
    Unknown_Test("Unknown Test"),
    IgnoredTest("IgnoredTest"),
    Resource_Optimism("Resource Optimism"),
    Magic_Number_Test("Magic Number Test"),
    Dependent_Test("Dependent Test"),
    ;

    private final String detectorName;
}
