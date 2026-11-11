// Behavioral tests for the Syncyard tray's pure logic.
//
// These tests exercise the actual MiniJson parser, compiled
// staleness defaults, and staleness decisions in SyncyardTray.cs.
// They are not part of the tray executable. tray-ci compiles this
// file together with SyncyardTray.cs and selects this class as
// the entry point with csc's /main option, so the tests run
// against the same assembly and can reach the tray's internal
// types without adding a project file or a new compile reference.
//
// C# 5 compatible on purpose, matching SyncyardTray.cs and the
// in-box Framework csc route.

using System;
using System.Globalization;
using System.IO;

namespace SyncyardTray
{
    internal static class BehaviorTests
    {
        private static int _checks;
        private static int _failures;

        public static int Main()
        {
            TestMiniJsonValidShapes();
            TestMiniJsonMalformedShapes();
            TestMiniJsonNestedShapes();
            TestMiniJsonUnicodeEscapes();
            TestStalenessDefaultsAndBoundaries();
            TestFleetSnapshotStaleness();

            Console.WriteLine(
                "TRAY_BEHAVIOR_TESTS: " + _checks + " checks, " +
                _failures + " failures");
            if (_failures == 0)
            {
                Console.WriteLine("TRAY_BEHAVIOR_TESTS_OK");
                return 0;
            }
            return 1;
        }

        private static void Check(string name, bool ok)
        {
            _checks++;
            Console.WriteLine((ok ? "PASS " : "FAIL ") + name);
            if (!ok) _failures++;
        }

        private static void Equal(string name, object expected,
            object actual)
        {
            bool ok = Object.Equals(expected, actual);
            if (!ok)
            {
                Console.WriteLine("  expected: " +
                    (expected == null ? "<null>" : expected.ToString()));
                Console.WriteLine("  actual:   " +
                    (actual == null ? "<null>" : actual.ToString()));
            }
            Check(name, ok);
        }

        private static void TestMiniJsonValidShapes()
        {
            string text = "{\"mode\":\"live\",\"count\":42," +
                "\"enabled\":true,\"ratio\":\"not-a-number\"}";
            Equal("MiniJson reads a valid string",
                "live", MiniJson.GetString(text, "mode"));
            Equal("MiniJson reads a valid integer",
                42, MiniJson.GetInt(text, "count", -1));
            Equal("MiniJson reads a valid boolean",
                true, MiniJson.GetBool(text, "enabled", false));
            Check("MiniJson finds an existing key",
                MiniJson.HasKey(text, "count"));
            Check("MiniJson does not invent a missing key",
                !MiniJson.HasKey(text, "absent"));
            Equal("MiniJson missing string is null",
                null, MiniJson.GetString(text, "absent"));
            Equal("MiniJson missing integer uses the default",
                7, MiniJson.GetInt(text, "absent", 7));
            Equal("MiniJson missing boolean uses the default",
                true, MiniJson.GetBool(text, "absent", true));
        }

        private static void TestMiniJsonMalformedShapes()
        {
            string badValues = "{\"count\":\"abc\"," +
                "\"enabled\":\"yes\"}";
            Equal("MiniJson malformed integer uses the default",
                9, MiniJson.GetInt(badValues, "count", 9));
            Equal("MiniJson malformed boolean uses the default",
                false, MiniJson.GetBool(badValues, "enabled", false));
            Equal("MiniJson unterminated string is null",
                null, MiniJson.GetString(
                    "{\"name\":\"unterminated", "name"));
            Equal("MiniJson unbalanced object is null",
                null, MiniJson.GetObject(
                    "{\"outer\":{\"inner\":{}}", "outer"));
            Equal("MiniJson null text string is null",
                null, MiniJson.GetString(null, "name"));
            Equal("MiniJson null text integer uses the default",
                3, MiniJson.GetInt(null, "count", 3));
        }

        private static void TestMiniJsonNestedShapes()
        {
            string text = "{\"outer\":{\"inner\":{" +
                "\"name\":\"nested\",\"value\":7},\"flag\":true}," +
                "\"waiting\":[{\"key\":\"one\",\"priority\":1}," +
                "{\"key\":\"two\",\"priority\":2}]," +
                "\"focus\":{\"keys\":[\"alpha\",\"beta\"]}}";
            string outer = MiniJson.GetObject(text, "outer");
            Check("MiniJson extracts a nested object",
                outer != null);
            string inner = MiniJson.GetObject(outer, "inner");
            Equal("MiniJson reads a string two objects deep",
                "nested", MiniJson.GetString(inner, "name"));
            Equal("MiniJson reads an integer two objects deep",
                7, MiniJson.GetInt(inner, "value", -1));
            Equal("MiniJson reads a sibling boolean in the outer object",
                true, MiniJson.GetBool(outer, "flag", false));

            var waiting = MiniJson.GetObjects(text, "waiting");
            Equal("MiniJson reads both objects in a nested array",
                2, waiting.Count);
            if (waiting.Count == 2)
            {
                Equal("MiniJson reads the first array object's key",
                    "one", MiniJson.GetString(waiting[0], "key"));
                Equal("MiniJson reads the second array object's value",
                    2, MiniJson.GetInt(waiting[1], "priority", -1));
            }

            string focus = MiniJson.GetObject(text, "focus");
            var keys = MiniJson.GetStringArray(focus, "keys");
            Equal("MiniJson reads a nested string array",
                "alpha,beta", String.Join(",", keys.ToArray()));

            string lanes = "{\"quick\":{\"cap\":1}," +
                "\"long\":{\"cap\":2}}";
            Equal("MiniJson lists object keys without nested keys",
                "quick,long", String.Join(",",
                    MiniJson.GetObjectKeys(lanes).ToArray()));
        }

        private static void TestMiniJsonUnicodeEscapes()
        {
            string text = "{\"name\":\"caf\\u00e9 \\u263A " +
                "\\\"quoted\\\" \\\\ line\\nnext\"}";
            Equal("MiniJson decodes unicode and standard escapes",
                "caf\u00e9 \u263A \"quoted\" \\ line\nnext",
                MiniJson.GetString(text, "name"));
            Equal("MiniJson decodes a unicode escape in an array",
                "\u263A", MiniJson.GetStringArray(
                    "{\"marks\":[\"\\u263A\"]}", "marks")[0]);
        }

        private static void TestStalenessDefaultsAndBoundaries()
        {
            var settings = new TraySettings();
            Equal("compiled fleet staleness default is 900 seconds",
                900, settings.FleetStaleSeconds);
            Equal("compiled connection staleness default is 600 seconds",
                600, settings.ConnectionStaleSeconds);
            Equal("compiled connection down default is 900 seconds",
                900, settings.ConnectionDownSeconds);

            int stale = settings.ConnectionStaleSeconds;
            int down = settings.ConnectionDownSeconds;
            Equal("no signal is disconnected",
                ConnectionStatus.Disconnected,
                ConnectionStatus.StateForAge(false, 0, stale, down));
            Equal("age just below the stale bound is connected",
                ConnectionStatus.Connected,
                ConnectionStatus.StateForAge(true, stale - 0.001,
                    stale, down));
            Equal("age exactly at the stale bound is connected",
                ConnectionStatus.Connected,
                ConnectionStatus.StateForAge(true, stale, stale, down));
            Equal("age just above the stale bound is stale",
                ConnectionStatus.Stale,
                ConnectionStatus.StateForAge(true, stale + 0.001,
                    stale, down));
            Equal("age exactly at the down bound is stale",
                ConnectionStatus.Stale,
                ConnectionStatus.StateForAge(true, down, stale, down));
            Equal("age just above the down bound is disconnected",
                ConnectionStatus.Disconnected,
                ConnectionStatus.StateForAge(true, down + 0.001,
                    stale, down));
        }

        private static void TestFleetSnapshotStaleness()
        {
            string path = Path.Combine(Path.GetTempPath(),
                "syncyard-tray-test-" +
                Guid.NewGuid().ToString("N") + ".json");
            try
            {
                WriteSnapshot(path, DateTime.UtcNow);
                var fresh = FleetStatus.Load(path, 900);
                Check("a current fleet snapshot is not stale",
                    fresh.HasData && !fresh.Stale);

                WriteSnapshot(path,
                    DateTime.UtcNow.AddSeconds(-901));
                var old = FleetStatus.Load(path, 900);
                Check("a fleet snapshot past 900 seconds is stale",
                    old.HasData && old.Stale);
            }
            finally
            {
                try { if (File.Exists(path)) File.Delete(path); }
                catch { }
            }
        }

        private static void WriteSnapshot(string path, DateTime generated)
        {
            string stamp = generated.ToString(
                "yyyy-MM-ddTHH:mm:ssZ",
                CultureInfo.InvariantCulture);
            File.WriteAllText(path,
                "{\"mode\":\"live\",\"generated_at\":\"" +
                stamp + "\"}");
        }
    }
}
